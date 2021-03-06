import logging

import cupy as cp

from tike.linalg import lstsq, projection, norm

from ..position import update_positions_pd
from ..probe import orthogonalize_eig, get_varying_probe, update_eigen_probe

logger = logging.getLogger(__name__)


def lstsq_grad(
    op, comm,
    data, probe, scan, psi,
    recover_psi=True, recover_probe=False, recover_positions=False,
    cg_iter=4,
    cost=None,
    eigen_probe=None,
    eigen_weights=None,
):  # yapf: disable
    """Solve the ptychography problem using Odstrcil et al's approach.

    The near- and farfield- ptychography problems are solved separately using
    gradient descent in the farfield and linear-least-squares in the nearfield.

    Parameters
    ----------
    op : tike.operators.Ptycho
        A ptychography operator.
    comm : tike.communicators.Comm
        An object which manages communications between GPUs and nodes.

    References
    ----------
    Michal Odstrcil, Andreas Menzel, and Manuel Guizar-Sicaros. Iterative
    least-squares solver for generalized maximum-likelihood ptychography.
    Optics Express. 2018.

    """
    data_ = data[0]
    probe = probe[0]
    scan_ = scan[0]
    psi = psi[0]
    if eigen_probe is not None:
        eigen_weights = eigen_weights[0]
        eigen_probe = eigen_probe[0]

    # -------------------------------------------------------------------------

    common_probe = probe
    unique_probe = get_varying_probe(
        probe,
        eigen_probe=eigen_probe,
        weights=eigen_weights,
    )

    farplane, cost = _update_wavefront(op, data_, unique_probe, scan_, psi)

    pad, end = op.diffraction.pad, op.diffraction.end

    # Solve the nearplane problem ---------------------------------------------

    for m in range(probe.shape[-3]):

        nearplane = farplane[..., m:m + 1, pad:end, pad:end]

        cprobe = common_probe[..., m:m + 1, :, :]
        uprobe = unique_probe[..., m:m + 1, :, :]

        patches = op.diffraction._patch(
            patches=cp.zeros(nearplane.shape, dtype='complex64'),
            psi=psi,
            scan=scan_,
            fwd=True,
        )

        # χ (diff) is the target for the nearplane problem; the difference
        # between the desired nearplane and the current nearplane that we wish
        # to minimize.
        diff = nearplane - uprobe * patches

        logger.info('%10s cost is %+12.5e', 'nearplane', norm(diff))

        if recover_psi:
            grad_psi = cp.conj(uprobe) * diff

            # (25b) Common object gradient. Use a weighted (normalized) sum
            # instead of division as described in publication to improve
            # numerical stability.
            common_grad_psi = op.diffraction._patch(
                patches=grad_psi,
                psi=cp.zeros(psi.shape, dtype='complex64'),
                scan=scan_,
                fwd=False,
            )

            dOP = op.diffraction._patch(
                patches=cp.zeros(patches.shape, dtype='complex64'),
                psi=common_grad_psi,
                scan=scan_,
                fwd=True,
            ) * uprobe
            A1 = cp.sum((dOP * dOP.conj()).real + 0.5, axis=(-2, -1))
            A1 += 0.5 * cp.mean(A1, axis=-3, keepdims=True)
            b1 = cp.sum((dOP.conj() * diff).real, axis=(-2, -1))

        if recover_probe:
            grad_probe = cp.conj(patches) * diff

            # (25a) Common probe gradient. Use simple average instead of
            # division as described in publication because that's what
            # ptychoshelves does
            common_grad_probe = cp.mean(
                grad_probe,
                axis=-5,
                keepdims=True,
            )

            dPO = common_grad_probe * patches
            A4 = cp.sum((dPO * dPO.conj()).real + 0.5, axis=(-2, -1))
            A4 += 0.5 * cp.mean(A4, axis=-3, keepdims=True)
            b2 = cp.sum((dPO.conj() * diff).real, axis=(-2, -1))

        if recover_probe and eigen_probe is not None:
            logger.info('Updating eigen probes')
            # (30) residual probe updates
            R = grad_probe - cp.mean(grad_probe, axis=-5, keepdims=True)

            for c in range(eigen_probe.shape[-4]):

                eigen_probe[..., c:c + 1, m:m + 1, :, :] = update_eigen_probe(
                    R,
                    eigen_probe[..., c:c + 1, m:m + 1, :, :],
                    eigen_weights[..., c, m],
                    β=0.01,  # TODO: Adjust according to mini-batch size
                )

                # Determine new eigen_weights for the updated eigen probe
                phi = patches * eigen_probe[..., c:c + 1, m:m + 1, :, :]
                n = cp.mean(
                    cp.real(diff * phi.conj()),
                    axis=(-1, -2),
                    keepdims=True,
                )
                norm_phi = cp.square(cp.abs(phi))
                d = cp.mean(norm_phi, axis=(-1, -2), keepdims=True)
                d += 0.1 * cp.mean(d, axis=-5, keepdims=True)
                weight_update = (n / d).reshape(*eigen_weights[..., 0, 0].shape)
                assert cp.all(cp.isfinite(weight_update))

                # (33) The sum of all previous steps constrained to zero-mean
                eigen_weights[..., c, m] += weight_update
                eigen_weights[..., c, m] -= cp.mean(
                    eigen_weights[..., c, m],
                    axis=-1,
                    keepdims=True,
                )

                if eigen_probe.shape[-4] <= c + 1:
                    # Subtract projection of R onto new probe from R
                    R -= projection(
                        R,
                        eigen_probe[..., c:c + 1, m:m + 1, :, :],
                        axis=(-2, -1),
                    )

        # (22) Use least-squares to find the optimal step sizes simultaneously
        if recover_psi and recover_probe:
            A2 = cp.sum((dOP * dPO.conj()), axis=(-2, -1))
            A3 = A2.conj()
            determinant = A1 * A4 - A2 * A3
            x1 = -cp.conj(A2 * b2 - A4 * b1) / determinant
            x2 = cp.conj(A1 * b2 - A3 * b1) / determinant
        elif recover_psi:
            x1 = b1 / A1
        elif recover_probe:
            x2 = b2 / A4

        # Update each direction
        if recover_psi:
            step = x1[..., None, None]

            # (27b) Object update
            weighted_step = cp.mean(step, keepdims=True, axis=-5)[..., 0, 0, 0]

            psi += weighted_step * common_grad_psi

        if recover_probe:
            step = x2[..., None, None]

            # (27a) Probe update
            cprobe += common_grad_probe * cp.mean(
                step,
                axis=-5,
                keepdims=True,
            )

        if __debug__:
            patches = op.diffraction._patch(
                patches=cp.zeros(nearplane.shape, dtype='complex64'),
                psi=psi,
                scan=scan_,
                fwd=True,
            )
            logger.info('%10s cost is %+12.5e', 'nearplane',
                        norm(cprobe * patches - nearplane))

    result = {
        'psi': [psi],
        'probe': [probe],
        'cost': cost,
        'scan': scan,
    }
    if eigen_probe is not None:
        result['eigen_probe'] = [eigen_probe]
        result['eigen_weights'] = [eigen_weights]
    return result


def _update_wavefront(op, data, varying_probe, scan, psi):

    # Compute the diffraction patterns for all of the probe modes at once.
    # We need access to all of the modes of a position to solve the phase
    # problem. The Ptycho operator doesn't do this natively, so it's messy.
    # TODO: Refactor so no using non-public API (_patch)
    patches = cp.zeros(data.shape, dtype='complex64')
    patches = op.diffraction._patch(
        patches=patches,
        psi=psi,
        scan=scan,
        fwd=True,
    )
    patches = patches.reshape(op.ntheta, scan.shape[-2], 1, 1,
                              op.detector_shape, op.detector_shape)

    nearplane = cp.tile(patches, reps=(1, 1, 1, varying_probe.shape[-3], 1, 1))
    pad, end = op.diffraction.pad, op.diffraction.end
    nearplane[..., pad:end, pad:end] *= varying_probe

    # Solve the farplane phase problem ----------------------------------------
    farplane = op.propagation.fwd(nearplane, overwrite=True)
    intensity = cp.sum(cp.square(cp.abs(farplane)), axis=(2, 3))
    cost = op.propagation.cost(data, intensity)
    logger.info('%10s cost is %+12.5e', 'farplane', cost)
    farplane -= 0.5 * op.propagation.grad(data, farplane, intensity)

    if __debug__:
        intensity = cp.sum(cp.square(cp.abs(farplane)), axis=(2, 3))
        cost = op.propagation.cost(data, intensity)
        logger.info('%10s cost is %+12.5e', 'farplane', cost)
        # TODO: Only compute cost every 20 iterations or on a log sampling?

    farplane = op.propagation.adj(farplane, overwrite=True)

    return farplane, cost
