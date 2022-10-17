"""High-level functions for running MCMC chains."""
import logging
import numpy as np
import scipy.stats as stats
from cmath import log
from concurrent.futures import ProcessPoolExecutor
from os import mkdir, path
from py21cmfast import yaml
from py21cmfast._utils import ParameterError

from .cosmoHammer import (
    CosmoHammerSampler,
    HDFStorageUtil,
    LikelihoodComputationChain,
    Params,
)

logger = logging.getLogger("21cmFAST")


def build_computation_chain(core_modules, likelihood_modules, params=None, setup=True):
    """
    Build a likelihood computation chain from core and likelihood modules.

    Parameters
    ----------
    core_modules : list
        A list of objects which define the necessary methods to be core modules
        (see :mod:`~py21cmmc.core`).
    likelihood_modules : list
        A list of objects which define the necessary methods to be likelihood modules (see
        :mod:`~py21cmmc.likelihood`)
    params : :class:`~py21cmmc.cosmoHammer.Params`, optional
        If provided, parameters which will be sampled by the chain.

    Returns
    -------
    chain : :class:`~py21cmmc.cosmoHammer.LikelihoodComputationChain`
    """
    if not hasattr(core_modules, "__len__"):
        core_modules = [core_modules]

    if not hasattr(likelihood_modules, "__len__"):
        likelihood_modules = [likelihood_modules]

    chain = LikelihoodComputationChain(params)

    for cm in core_modules:
        chain.addCoreModule(cm)

    for lk in likelihood_modules:
        chain.addLikelihoodModule(lk)

    if setup:
        chain.setup()
    return chain


def run_mcmc(
    core_modules,
    likelihood_modules,
    params,
    datadir=".",
    model_name="21CMMC",
    continue_sampling=True,
    reuse_burnin=True,
    log_level_21CMMC=None,
    sampler_cls=CosmoHammerSampler,
    use_multinest=False,
    use_zeus=False,
    **mcmc_options,
) -> CosmoHammerSampler:
    r"""Run an MCMC chain.

    Parameters
    ----------
    core_modules : list
        A list of objects which define the necessary methods to be core modules (see
        :mod:`~py21cmmc.core`).
    likelihood_modules : list
        A list of objects which define the necessary methods to be likelihood modules
        (see :mod:`~py21cmmc.likelihood`)
    params : dict
        Parameters which will be sampled by the chain. Each entry's key specifies the
        name of the parameter, and its value is an iterable `(val, min, max, width)`,
        with `val` the initial guess, `min` and `max` the hard boundaries on the
        parameter's value, and `width` determining the size of the initial ball of
        walker positions for the parameter.
    datadir : str, optional
        Directory to which MCMC info will be written (eg. logs and chain files)
    model_name : str, optional
        Name of the model, which determines filenames of outputs.
    continue_sampling : bool, optional
        If an output chain file can be found that matches these inputs, sampling can be
        continued from its last iteration, up to the number of iterations specified. If
        set to `False`, any output file which matches these parameters will have its
        samples over-written.
    reuse_burnin : bool, optional
        If a pre-computed chain file is found, and `continue_sampling=False`, setting
        `reuse_burnin` will salvage the burnin part of the chain for re-use, but
        re-compute the samples themselves.
    log_level_21CMMC : (int or str, optional)
        The logging level of the cosmoHammer log file.
    use_multinest : bool, optional
        If true, use the MultiNest sampler instead.
    use_zeus : bool, optional
        If true, use the zeus sampler instead.

    Other Parameters
    ----------------
    \*\*mcmc_options:
        All other parameters are passed directly to
        :class:`~py21cmmc.cosmoHammer.CosmoHammerSampler`. These include important
        options such as ``walkersRatio`` (the number of walkers is
        ``walkersRatio*nparams``), ``sampleIterations``, ``burninIterations``, ``pool``,
        ``log_level_stream`` and ``threadCount``.
        If use_multinest, parameters required by MultiNest as shown below should be
        provided here.
    n_live_points : int, optional
        number of live points
    importance_nested_sampling : bool, optional
        If True, Multinest will use Importance Nested Sampling (INS).
    sampling_efficiency : float, optional
        defines the sampling efficiency. 0.8 and 0.3 are recommended for parameter
        estimation & evidence evalutation
    evidence_tolerance : float, optional
        A value of 0.5 should give good enough accuracy.
    max_iter : int, optional
        maximum number of iterations. 0 is unlimited.
    multimodal : bool, optional
        whether or not to detect multi mode
    write_output : bool, optional
        write output files? This is required for analysis.

        If use_zeus, parameters required by zeus as shown below should be
        provided here.
    nsteps : int
        number of steps per iteration
    ndim : int
        number of dimensions to sampler over
    nwalkers : int
        number of walkers
    tolerance : float, optional
        Tuning optimization tolerance (Default is 0.05).
    patience : int, optional
        Number of tuning steps to wait to make sure that tuning is done (Default is 5).
    maxsteps : int, optional
        Number of maximum stepping-out steps (Default is 10^4).
    mu : float, optional
        Scale factor (Default value is 1.0), this will be tuned if tune=True.
    maxiter : int, optional
        Number of maximum Expansions/Contractions (Default is 10^4).
    pool : bool, optional
        External pool of workers to distribute workload to multiple CPUs (default is None).
    vectorize : bool, optional
        If true (default is False), logprob_fn receives not just one point but an array of points, and returns an array of log-probabilities.
    blobs_dtype : list, optional
        List containing names and dtypes of blobs metadata e.g. [("log_prior", float), ("mean", float)].
        It's useful when you want to save multiple species of metadata. Default is None.
    verbose : bool, optional
        If True (default) print log statements.
    check_walkers : bool, optional
        If True (default) then check that nwalkers >= 2*ndim and even.
    shuffle_ensemble : bool, optional
        If True (default) then shuffle the ensemble of walkers in every iteration before splitting it.
    light_mode : bool, optional
        If True (default is False) then no expansions are performed after the tuning phase.
        This can significantly reduce the number of log likelihood evaluations but works best in target distributions that are apprroximately Gaussian.

    Returns
    -------
    sampler : :class:`~py21cmmc.cosmoHammer.CosmoHammerSampler` instance.
        The sampler object, from which the chain itself may be accessed (via the
        ``samples`` attribute). If use_multinest, return multinest sampler.
        If use_zeus, return zeus sampler.
    """
    file_prefix = path.join(datadir, model_name)

    # check that only one sampler is specified
    if use_multinest and use_zeus:
        raise ValueError("You cannot use_multinest and use_zeus at the same time!")

    if use_multinest:
        n_live_points = mcmc_options.get("n_live_points", 100)
        importance_nested_sampling = mcmc_options.get(
            "importance_nested_sampling", True
        )
        sampling_efficiency = mcmc_options.get("sampling_efficiency", 0.8)
        evidence_tolerance = mcmc_options.get("evidence_tolerance", 0.5)
        max_iter = mcmc_options.get("max_iter", 50)
        multimodal = mcmc_options.get("multimodal", True)
        write_output = mcmc_options.get("write_output", True)
        datadir = datadir + "/MultiNest/"
        try:
            from pymultinest import run
        except ImportError:
            raise ImportError("You need to install pymultinest to use this function!")
    try:
        mkdir(datadir)
    except FileExistsError:
        pass

    if use_zeus:
        ndim = mcmc_options.get(
            "ndim", 2
        )  # Number of parameters/dimensions (e.g. m and c)
        nwalkers = mcmc_options.get(
            "nwalkers", 10
        )  # Number of walkers to use. It should be at least twice the number of dimensions.
        nsteps = mcmc_options.get("nsteps", 100)  # Number of steps/iterations.
        # set up parameters
        params = Params(*[(k, v) for k, v in params.items()])
        # initial positions of the walkers
        start = np.asarray(
            [
                stats.truncnorm.rvs(
                    (params[i][1] - params[i][0]) / params[i][-1],
                    (params[i][2] - params[i][0]) / params[i][-1],
                    loc=params[i][0],
                    scale=params[i][-1],
                    size=nwalkers,
                )
                for i in range(ndim)
            ]
        ).T
        tolerance = mcmc_options.get("tolerance", 0.05)
        patience = mcmc_options.get("patience", 5)
        maxsteps = mcmc_options.get("maxsteps", 1e4)
        mu = mcmc_options.get("mu", 1.0)
        maxiter = mcmc_options.get("maxiter", 1e4)
        pool = mcmc_options.get("pool", None)
        vectorize = mcmc_options.get("vectorize", False)
        blobs_dtype = mcmc_options.get("blobs_dtype", None)
        verbose = mcmc_options.get("vectorize", True)
        check_walkers = mcmc_options.get("check_walkers", True)
        shuffle_ensemble = mcmc_options.get("shuffle_ensemble", True)
        light_mode = mcmc_options.get("light_mode", False)
        print(start.shape)
        try:
            import zeus
        except ImportError:
            raise ImportError("You need to install zeus to use this function!")

    # Setup parameters.
    if not isinstance(params, Params):
        params = Params(*[(k, v) for k, v in params.items()])

    chain = build_computation_chain(
        core_modules, likelihood_modules, params, setup=False
    )

    if continue_sampling and not (use_multinest or use_zeus):
        try:
            with open(file_prefix + ".LCC.yml", "r") as f:
                old_chain = yaml.load(f)

            if old_chain != chain:
                raise RuntimeError(
                    "Attempting to continue chain, but chain parameters are different. "
                    + "Check your parameters against {file_prefix}.LCC.yml".format(
                        file_prefix=file_prefix
                    )
                )

        except FileNotFoundError:
            pass

        # We need to ensure that simulate=False if trying to continue sampling.
        for lk in chain.getLikelihoodModules():
            if hasattr(lk, "_simulate") and lk._simulate:
                logger.warning(
                    """
                    Likelihood {} was defined to re-simulate data/noise, but this is incompatible with
                    `continue_sampling`. Setting simulate=False and continuing...
                    """
                )
                lk._simulate = False

    # Write out the parameters *before* setup.
    # TODO: not sure if this is the best idea -- should it be after setup()?
    try:
        with open(file_prefix + ".LCC.yml", "w") as f:
            yaml.dump(chain, f)
    except Exception as e:
        logger.warning(
            "Attempt to write out YAML file containing LikelihoodComputationChain failed. "
            "Boldly continuing..."
        )
        print(e)

    chain.setup()

    # Set logging levels
    if log_level_21CMMC is not None:
        logging.getLogger("21CMMC").setLevel(log_level_21CMMC)

    if use_multinest:

        def likelihood(p, ndim, nparams):
            try:
                return chain.computeLikelihoods(
                    chain.build_model_data(
                        Params(*[(k, v) for k, v in zip(params.keys, p)])
                    )
                )
            except ParameterError:
                return -np.inf

        def prior(p, ndim, nparams):
            for i in range(ndim):
                p[i] = params[i][1] + p[i] * (params[i][2] - params[i][1])

        try:
            sampler = run(
                likelihood,
                prior,
                n_dims=len(params.keys),
                n_params=len(params.keys),
                n_live_points=n_live_points,
                resume=continue_sampling,
                write_output=write_output,
                outputfiles_basename=datadir + model_name,
                max_iter=max_iter,
                importance_nested_sampling=importance_nested_sampling,
                multimodal=multimodal,
                evidence_tolerance=evidence_tolerance,
                sampling_efficiency=sampling_efficiency,
                init_MPI=False,
            )
            return 1

        except OSError:  # pragma: nocover
            raise ImportError(
                "You also need to build MultiNest library. See https://johannesbuchner.github.io/PyMultiNest/install.html#id4 for more information."
            )

    elif use_zeus:

        def prior(p):
            for i in range(len(p)):
                if (p[i] > params[i][2]) or (p[i] < params[i][1]):
                    return p, True
            return p, False

        def likelihood(p):
            try:
                return chain.computeLikelihoods(
                    chain.build_model_data(
                        Params(*[(k, v) for k, v in zip(params.keys, p)])
                    )
                )
            except ParameterError:
                return -np.inf

        def posterior(p):
            # pass point into prior to check if in bounds
            p, inf = prior(p)
            if inf:
                return -np.inf
            # if in bounds, evaluate likelihood
            log_prob = likelihood(p)
            return log_prob

        sampler = zeus.EnsembleSampler(
            nwalkers,
            ndim,
            posterior,
            tolerance=tolerance,
            patience=patience,
            maxsteps=maxsteps,
            mu=mu,
            maxiter=maxiter,
            pool=pool,
            vectorize=vectorize,
            blobs_dtype=blobs_dtype,
            verbose=verbose,
            check_walkers=check_walkers,
            shuffle_ensemble=shuffle_ensemble,
            light_mode=light_mode,
        )  # Initialise the sampler
        sampler.run_mcmc(start, nsteps)  # Run sampling
        return sampler

    else:
        pool = mcmc_options.pop(
            "pool",
            ProcessPoolExecutor(max_workers=mcmc_options.get("threadCount", 1)),
        )
        sampler = sampler_cls(
            continue_sampling=continue_sampling,
            likelihoodComputationChain=chain,
            storageUtil=HDFStorageUtil(file_prefix),
            filePrefix=file_prefix,
            reuseBurnin=reuse_burnin,
            pool=pool,
            **mcmc_options,
        )

        # The sampler writes to file, so no need to save anything ourselves.
        sampler.startSampling()

        return sampler
