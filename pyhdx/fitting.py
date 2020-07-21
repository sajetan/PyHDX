from pyhdx.support import get_constant_blocks, get_reduced_blocks, temporary_seed
# from pyhdx.fitting_tf import CurveFit, TFParameter, L1L2Differential, LossHistory, AssociationPFactFunc, \
#     AssociationRateFunc, TFFitResult, EarlyStopping, Adagrad
from scipy.optimize import fsolve
import numpy as np
from symfit import Fit, Variable, Parameter, exp, Model, CallableModel
from symfit.core.minimizers import DifferentialEvolution, Powell
from collections import namedtuple
from functools import reduce, partial
from operator import add
from dask.distributed import Client



class KineticsModel(object):
    """
    Base class for all Kinetics models. Main function is to generate :ref:`symfit` Variables and Parameters. The class
    attributes `par_index` and `var_index` are used to make sure names used by :ref:`symfit` are unique and their
    mapping to user-defined names are stored in the `names` dictionary.

    Parameters
    ----------

    bounds : :obj:`tuple`
        Tuple of default `min`, `max` parameters to use.

    Attributes
    ----------

    names : :obj:`dict`
        Dictionary which maps human-readable names (keys) to dummy names (values)
    sf_model : :class:`~symfit.Model`
        The `symfit` model which describes this model. Implemented by subclasses.

    """

    par_index = 0
    var_index = 0

    def __init__(self, bounds):
        if bounds[1] < bounds[0]:
            raise ValueError('Lower bound must be smaller than upper bound')
        self.bounds = bounds
        self.names = {}  # human name: dummy name
        self.sf_model = None

    def make_parameter(self, name, value=None, min=None, max=None):
        """
        Create a new :class:~symfit.Parameter.

        Parameters
        ----------
        name: :obj:`str`
            Human-readable name for the parameter
        value: :obj:`float`
            Initial guess value
        min: :obj:`float`
            Lower bound value. If `None`, the value from `bounds` is used.
        max: :obj:`float`
            Lower bound value. If `None`, the value from `bounds` is used.

        Returns
        -------
        p : :class:`~symfit.Parameter`

        """
        min = min if min is not None else self.bounds[0]
        max = max if max is not None else self.bounds[1]

        value = value or np.mean(self.bounds)
        dummy_name = 'pyhdx_par_{}'.format(self.par_index)
        KineticsModel.par_index += 1
        p = Parameter(dummy_name, value=value, min=min, max=max)
        self.names[name] = dummy_name
        return p

    def make_variable(self, name):
        """
        Create a new :class:~symfit.Variable.

        Parameters
        ----------
        name: :obj:`str`
            Human-readable name for the variable

        Returns
        -------
        p : :class:`~symfit.Variable`

        """
        dummy_name = 'pyhdx_var_{}'.format(self.var_index)
        KineticsModel.var_index += 1
        v = Variable(dummy_name)
        self.names[name] = dummy_name
        return v

    @property
    def r_names(self):
        """:obj:`dict`: Reverse dictionary of the variable and parameter names"""
        return {v: k for k, v in self.names.items()}

    def get_parameter(self, name):
        """
        Get the parameter with the Human-readable name `name`

        Parameters
        ----------
        name : :obj:`str`
            Name of the parameter to retrieve

        Returns
        -------
        parameter : :class:`~symfit.Parameter`

        """
        dummy_name = self.names[name]
        par_names = list([p.name for p in self.sf_model.params])
        idx = par_names.index(dummy_name)
        parameter = self.sf_model.params[idx]
        return parameter


class SingleKineticModel(KineticsModel):
    """
    Base class for models which fit only a single set (slice) of time, uptake points
    """


class TwoComponentAssociationModel(SingleKineticModel):
    """Two componenent Association"""
    def __init__(self, bounds):
        super(TwoComponentAssociationModel, self).__init__(bounds)

        r = self.make_parameter('r', value=0.5, min=0, max=1)
        k1 = self.make_parameter('k1')
        k2 = self.make_parameter('k2')
        t = self.make_variable('t')
        y = self.make_variable('y')

        self.sf_model = Model({y: 100 * (1 - (r * exp(-k1*t) + (1 - r) * exp(-k2*t)))})

    def __call__(self, t, **params):
        """call model at time t, returns uptake values of peptides"""
        time_var = self.names['t']
        params[time_var] = t
        return self.sf_model(**params)

    def initial_guess(self, t, d):
        """
        Calculates initial guesses for fitting of two-component kinetic uptake reaction

        Parameters
        ----------
        t : :class:~`numpy.ndarray`
            Array with time points
        d : :class:~`numpy.ndarray`
            Array with uptake values

        """
        k1_v = fsolve(func_short_ass, 1 / 2, args=(t[2], d[2]))[0]
        k2_v = fsolve(func_long_ass, 1 / 20, args=(t[-2], d[-2], k1_v))[0]

        k1_p = self.get_parameter('k1')
        k1_p.value = k1_v
        k2_p = self.get_parameter('k2')
        k2_p.value = k2_v
        r_p = self.get_parameter('r')
        r_p.value = 0.5

    def initial_grid(self, t, d, step=15):
        kmax = 5 * np.log(1-0.98) / -t[1]
        d_final = np.min([0.95, d[-1]/100])  # todo refactor norm
        kmin = np.log(1-d_final) / -t[-1]

        tau_space = np.logspace(np.log10(1/kmax), np.log10(1/kmin), num=step, endpoint=True)
        r_space = np.linspace(0.05, 0.95, num=step, endpoint=True)

        guess = np.column_stack([tau_space, tau_space, r_space])
        return guess

    def get_rate(self, **params):
        """

        Parameters
        ----------
        params

        key value where keys are the dummy names

        Returns
        -------

        """

        #todo generalize duplicate code for other models
        r = params[self.names['r']]
        k1 = params[self.names['k1']]
        k2 = params[self.names['k2']]

        #tau = r * tau1 + (1 - r) * tau2

        x0 = k1 if r > 0.5 else k2

        t_log = fsolve(self.min_func, np.log(x0), args=(k1, k2, r))
        try:
            assert np.round(self.min_func(t_log, k1, k2, r), 5) == 0, 'Failed to find half life root'
        except AssertionError:
            print('uhoh')
            print(k1, k2, r)
            print(np.round(self.min_func(t_log, k1, k2, r), 5) == 0)
        k = np.asscalar(np.log(2) / np.exp(t_log))

        return k

    def get_tau(self, **params):
        """

        Parameters
        ----------
        params

        key value where keys are the dummy names

        Returns
        -------

        """
        k = self.get_rate(**params)
        return 1/k

    @staticmethod
    def min_func(t_log, k1, k2, r):
        t = np.exp(t_log)
        return 0.5 - r * np.exp(-k1*t) - (1 - r) * np.exp(-k2*t)


class OneComponentAssociationModel(SingleKineticModel):
    """One component Association"""
    def __init__(self, bounds):
        super(OneComponentAssociationModel, self).__init__(bounds)
        k1 = self.make_parameter('k1')
        t = self.make_variable('t')
        y = self.make_variable('y')

        self.sf_model = Model({y: 100 * (1 - exp(-k1*t))})

    def __call__(self, t, **params):
        """call model at time t, returns uptake values of peptides"""

        time_var = self.names['t']
        params[time_var] = t
        return self.sf_model(**params)

    def initial_guess(self, t, d):
        """
        Calculates initial guesses for fitting of two-component kinetic uptake reaction

        Parameters
        ----------
        t : :class:~`numpy.ndarray`
            Array with time points
        d : :class:~`numpy.ndarray`
            Array with uptake values

        """
        k1_v = fsolve(func_short_ass, 1 / 2, args=(t[3], d[3]))[0]

        k1_p = self.get_parameter('k1')
        k1_p.value = k1_v

    def get_rate(self, **params):
        k1 = params[self.names['k1']]
        return k1

    def get_tau(self, **params):
        k = self.get_rate(**params)
        return 1/k


class TwoComponentDissociationModel(SingleKineticModel):
    """Two componenent Association"""
    def __init__(self, bounds):
        super(TwoComponentDissociationModel, self).__init__(bounds)

        r = self.make_parameter('r', value=0.5, min=0, max=1)
        k1 = self.make_parameter('k1')
        k2 = self.make_parameter('k2')
        t = self.make_variable('t')
        y = self.make_variable('y')

        self.sf_model = Model({y: 100 * (r * exp(-k1*t) + (1 - r) * exp(-k2*t))})

    def __call__(self, t, **params):
        """call model at time t, returns uptake values of peptides"""
        time_var = self.names['t']
        params[time_var] = t
        return self.sf_model(**params)

    def initial_guess(self, t, d):
        """
        Calculates initial guesses for fitting of two-component kinetic uptake reaction

        Parameters
        ----------
        t : :class:~`numpy.ndarray`
            Array with time points
        d : :class:~`numpy.ndarray`
            Array with uptake values

        """
        k1_v = fsolve(func_short_ass, 1 / 2, args=(t[2], d[2]))[0]
        k2_v = fsolve(func_long_ass, 1 / 20, args=(t[-2], d[-2], k1_v))[0]

        k1_p = self.get_parameter('k1')
        k1_p.value = k1_v
        k2_p = self.get_parameter('k2')
        k2_p.value = k2_v
        r_p = self.get_parameter('r')
        r_p.value = 0.5

    def initial_grid(self, t, d, step=15):
        kmax = 5 * np.log(1-0.98) / -t[1]
        d_final = np.min([0.95, d[-1]/100])  # todo refactor norm
        kmin = np.log(1-d_final) / -t[-1]

        tau_space = np.logspace(np.log10(1/kmax), np.log10(1/kmin), num=step, endpoint=True)
        r_space = np.linspace(0.05, 0.95, num=step, endpoint=True)

        guess = np.column_stack([tau_space, tau_space, r_space])
        return guess

    def get_rate(self, **params):
        """

        Parameters
        ----------
        params

        key value where keys are the dummy names

        Returns
        -------

        """

        #todo generalize duplicate code for other models
        r = params[self.names['r']]
        k1 = params[self.names['k1']]
        k2 = params[self.names['k2']]

        #tau = r * tau1 + (1 - r) * tau2

        x0 = k1 if r > 0.5 else k2

        t_log = fsolve(self.min_func, np.log(x0), args=(k1, k2, r))
        try:
            assert np.round(self.min_func(t_log, k1, k2, r), 5) == 0, 'Failed to find half life root'
        except AssertionError:
            print('uhoh')
            print(k1, k2, r)
            print(np.round(self.min_func(t_log, k1, k2, r), 5) == 0)
        k = np.asscalar(np.log(2) / np.exp(t_log))

        return k

    def get_tau(self, **params):
        """

        Parameters
        ----------
        params

        key value where keys are the dummy names

        Returns
        -------

        """
        k = self.get_rate(**params)
        return 1/k

    @staticmethod
    def min_func(t_log, k1, k2, r):
        t = np.exp(t_log)
        return - 0.5 + r * np.exp(-k1*t) + (1 - r) * np.exp(-k2*t)


class OneComponentDissociationModel(SingleKineticModel):
    """One component Association"""
    def __init__(self, bounds):
        super(OneComponentDissociationModel, self).__init__(bounds)
        k1 = self.make_parameter('k1')
        t = self.make_variable('t')
        y = self.make_variable('y')

        self.sf_model = Model({y: 100 * exp(-k1*t)})

    def __call__(self, t, **params):
        """call model at time t, returns uptake values of peptides"""

        time_var = self.names['t']
        params[time_var] = t
        return self.sf_model(**params)

    def initial_guess(self, t, d):
        """
        Calculates initial guesses for fitting of two-component kinetic uptake reaction

        Parameters
        ----------
        t : :class:~`numpy.ndarray`
            Array with time points
        d : :class:~`numpy.ndarray`
            Array with uptake values

        """
        k1_v = fsolve(func_short_ass, 1 / 2, args=(t[3], d[3]))[0]

        k1_p = self.get_parameter('k1')
        k1_p.value = k1_v

    def get_rate(self, **params):
        k1 = params[self.names['k1']]
        return k1

    def get_tau(self, **params):
        k = self.get_rate(**params)
        return 1/k


def func_short_dis(k, tt, A):
    """
    Function to estimate the fast time component

    Parameters
    ----------
    k : :obj:`float`
        Lifetime
    tt : :obj:`float`
        Selected time point
    A : :obj:`float`
        Target amplitude

    Returns
    -------
    A_t : :obj:`float`
        Amplitude difference given tau, tt, A

    """
    return 100 * np.exp(-k * tt) - A


def func_long_dis(k, tt, A, k1):
    """
    Function to estimate the short time component

    Parameters
    ----------
    k : :obj:`float`
        rate
    tt : :obj:`float`
        Selected time point
    A : :obj:`float`
        Target amplitude
    k1: : obj:`float`
        Rate of fast time component

    Returns
    -------
    A_t : :obj:`float`
        Amplitude difference given tau, tt, A, tau1

    """
    return 100 * (0.5 * np.exp(-k1*tt) + 0.5 * np.exp(-k*tt)) - A


def func_short_ass(k, tt, A):
    """
    Function to estimate the fast time component

    Parameters
    ----------
    k : :obj:`float`
        Lifetime
    tt : :obj:`float`
        Selected time point
    A : :obj:`float`
        Target amplitude

    Returns
    -------
    A_t : :obj:`float`
        Amplitude difference given tau, tt, A

    """
    return 100 * (1 - np.exp(-k * tt)) - A


def func_long_ass(k, tt, A, k1):
    """
    Function to estimate the short time component

    Parameters
    ----------
    k : :obj:`float`
        rate
    tt : :obj:`float`
        Selected time point
    A : :obj:`float`
        Target amplitude
    k1: : obj:`float`
        Rate of fast time component

    Returns
    -------
    A_t : :obj:`float`
        Amplitude difference given tau, tt, A, tau1

    """
    return 100 * (1 - (0.5 * np.exp(-k1*tt) + 0.5 * np.exp(-k*tt))) - A


EmptyResult = namedtuple('EmptyResult', ['chi_squared', 'params'])
er = EmptyResult(np.nan, {k: np.nan for k in ['tau1', 'tau2', 'r']})


def fit_kinetics(t, d, model, chisq_thd):
    """
    Fit time kinetics with two time components and corresponding relative amplitude.

    Parameters
    ----------
    t : :class:`~numpy.ndarray`
        Array of time points
    d : :class:`~numpy.ndarray`
        Array of uptake values
    chisq_thd: :obj:`float`
        Threshold chi squared above which the fitting is repeated with the Differential Evolution algorithm.

    Returns
    -------
    res : :class:`~symfit.FitResults`
        Symfit fitresults object.
    """
    if np.any(np.isnan(d)):
        raise ValueError('There shouldnt be NaNs anymore')
        er = EmptyResult(np.nan, {p.name: np.nan for p in model.sf_model.params})
        return er

    model.initial_guess(t, d)
    with temporary_seed(43):
        fit = Fit(model.sf_model, t, d, minimizer=Powell)
        res = fit.execute()

        if not check_bounds(res) or np.any(np.isnan(list(res.params.values()))) or res.chi_squared > chisq_thd:
            fit = Fit(model.sf_model, t, d, minimizer=DifferentialEvolution)
            #grid = model.initial_grid(t, d, step=5)
            res = fit.execute()

    return res


def check_bounds(fit_result):
    for param in fit_result.model.params:
        value = fit_result.params[param.name]
        if value < param.min:
            return False
        elif value > param.max:
            return False
    return True


def fit_global(data, model):
    fit = Fit(model.sf_model, **data)
    res = fit.execute()
    return res


class KineticsFitting(object):

    def __init__(self, k_series, bounds=None, temperature=None, pH=None, c_term=None, cluster=None):
        #todo check if coverage is the same!
        assert k_series.uniform

        self.k_series = k_series
        self.bounds = bounds or self._get_bounds()
        self.temperature = temperature
        self.pH = pH
        self.c_term = c_term
        self.cluster = cluster

    def _get_bounds(self):
        #todo document
        #todo this is now causing confusion when getting protection factors as output
        times = self.k_series.timepoints
        nonzero_times = times[np.nonzero(times)]
        t_first = np.min(nonzero_times)
        t_last = np.max(nonzero_times)
        b_upper = 5*np.log(2) / t_first
        b_lower = np.log(2) / (t_last * 5)

        return b_lower, b_upper

    def _prepare_blocks_fit(self, initial_result, model_type='association', block_func=get_reduced_blocks, **block_kwargs):
        split = self.k_series.split()

        models = []
        intervals = []
        d_list = []
        for section in split.values():
            s, e = section.cov.start, section.cov.end  #inclusive, exclusive
            intervals.append((s, e))  #inclusive, exclusive

            blocks = block_func(section.cov, **block_kwargs)
            model = LSQKinetics(initial_result, section, blocks, self.bounds, model_type=model_type)

            data_dict = {d_var.name: scores for d_var, scores in zip(model.d_vars, section.scores_peptides.T)}
            data_dict[model.t_var.name] = section.timepoints
            d_list.append(data_dict)

            models.append(model)

        return d_list, intervals, models

    async def blocks_fit_async(self, initial_result, pbar=None, model_type='association', block_func=get_reduced_blocks, **block_kwargs):
        """ initial_result: KineticsFitResult object from global_fitting"""
        assert self.k_series.uniform
        d_list, intervals, models = self._prepare_blocks_fit(initial_result, model_type=model_type, block_func=block_func, **block_kwargs)
        sf_models = list([m.sf_model for m in models])

        # for some reason if using the function fit_global from outer scope this doesnt work
        def func(model, data):
            fit = Fit(model, **data)
            res = fit.execute()
            return res

        client = await Client(self.cluster)
        futures = client.map(func, sf_models, d_list)
        if pbar:
            pbar.num_tasks = len(d_list)
            await pbar.run(futures)

        results = client.gather(futures)
        fit_result = KineticsFitResult(self.k_series, intervals, results, models)

        return fit_result

    def blocks_fit(self, initial_result, pbar=None, model_type='association', block_func=get_reduced_blocks, **block_kwargs):
        """ initial_result: KineticsFitResult object from global_fitting"""

        assert self.k_series.uniform

        d_list, intervals, models = self._prepare_blocks_fit(initial_result, model_type=model_type, block_func=block_func, **block_kwargs)

        results = []
        for data, model in zip(d_list, models):
            result = fit_global(data, model)
            if pbar:
                pbar.increment()
            results.append(result)

        fit_result = KineticsFitResult(self.k_series, intervals, results, models)

        return fit_result

    def _prepare_wt_avg_fit(self, model_type='association'):
        models = []
        intervals = []  # Intervals; (start, end); (inclusive, exclusive)
        d_list = []
        for k, series in self.k_series.split().items():
            arr = series.scores_stack.T
            i = 0
            # because intervals are inclusive, exclusive we need to add an extra entry to r_number for the final exclusive bound
            r_excl = np.append(series.cov.r_number, [series.cov.r_number[-1] + 1])

            for bl in series.cov.block_length:
                intervals.append((r_excl[i], r_excl[i + bl]))
                d = arr[i]
                d_list.append(d)
                if model_type == 'association':
                    model = TwoComponentAssociationModel(self.bounds)
                elif model_type == 'dissociation':
                    model = TwoComponentDissociationModel(self.bounds)
                else:
                    raise ValueError('Invalid model type {}'.format(model_type))
                # res = EmptyResult(np.nan, {p.name: np.nan for p in model.sf_model.params})

                models.append(model)
                i += bl  # increment in block length does not equal move to the next start position

        return d_list, intervals, models

    async def weighted_avg_fit_async(self, chisq_thd=20, model_type='association', pbar=None):
        """
        Block length _should_ be equal to the block length of all measurements in the series, provided that all coverage
        is the same

        Parameters
        ----------
        chisq_max

        Returns
        -------

        """

        d_list, intervals, models = self._prepare_wt_avg_fit(model_type=model_type)
        fit_func = partial(fit_kinetics, self.k_series.timepoints)
        client = await Client(self.cluster)
        futures = client.map(fit_func, d_list, models, chisq_thd=chisq_thd)
        if pbar:
            pbar.num_tasks = len(d_list)  #this call assignment might also need unlocked()
            await pbar.run(futures)

        results = client.gather(futures)

        fit_result = KineticsFitResult(self.k_series, intervals, results, models)
        return fit_result

    def weighted_avg_fit(self, chisq_thd=20, model_type='association', pbar=None, callbacks=None):
        """
        Block length _should_ be equal to the block length of all measurements in the series, provided that all coverage
        is the same

        Parameters
        ----------
        chisq_max

        Returns
        -------

        """

        d_list, intervals, models = self._prepare_wt_avg_fit(model_type=model_type)
        if pbar:
            self.num_tasks = len(d_list)
            inc = pbar.increment
        else:
            inc = lambda: None

        results = []
        for d, model in zip(d_list, models):
            result = fit_kinetics(self.k_series.timepoints, d, model, chisq_thd=chisq_thd)
            inc()
            results.append(result)

        fit_result = KineticsFitResult(self.k_series, intervals, results, models)
        return fit_result

    def global_fit(self, initial_result, use_kint=True, learning_rate=0.01, l1=1e2, l2=0., epochs=10000, callbacks=None):
        import pyhdx.fitting_tf as ftf

        #todo sessions
        #https: // stackoverflow.com / questions / 51747660 / running - different - models - in -one - script - in -tensorflow - 1 - 9

        intervals = []
        weights = []
        funcs = []
        inputs = []
        losses = []

        if use_kint:
            k_int = self.k_series.cov.calc_kint(self.temperature, self.pH, c_term=self.c_term)
            k_r_number = self.k_series.cov.sequence_r_number
            k_dict = {'r_number': k_r_number, 'k_int': k_int}
        else:
            k_dict = None

        gen = self._prepare_global_fit_gen(initial_result, k_int=k_dict, l1=l1, l2=l2)

        early_stop = ftf.EarlyStopping(monitor='loss', min_delta=0.1, patience=50)
        callbacks = [early_stop] if callbacks is None else callbacks
        if not np.any([isinstance(cb, ftf.EarlyStopping) for cb in callbacks]):
            callbacks.append(early_stop)

        for model, interval, input_data, output_data in gen:
            intervals.append(interval)
            func = model.layers[-1].function  # assuming the last layer is the fitting layer, other are input
            funcs.append(func)
            inputs.append(input_data)

            cb = ftf.LossHistory()
            #early_stop = ftf.EarlyStopping(monitor='loss', min_delta=0.1, patience=50)

            model.compile(loss='mse', optimizer=ftf.Adagrad(learning_rate=learning_rate))
            result = model.fit(input_data, output_data, verbose=0, epochs=epochs, callbacks=callbacks + [cb])
            losses.append(result.history['loss'])
            print('number of epochs', len(result.history['loss']))
            wts = np.squeeze(cb.weights[-1][0])  # weights are the first weights from the last layer
            weights.append(wts)

        tf_fitresult = ftf.TFFitResult(self.k_series, intervals, funcs, weights, inputs, loss=losses)

        return tf_fitresult

    def _prepare_global_fit_gen(self, initial_result, k_int=None, l1=1e2, l2=0.):
        """

        Parameters
        ----------
        initial_result numpy structured array with fields r_number, rate
        k_int optional numpy structured array with fields r_number, k_int

        Returns
        -------

        """
        import pyhdx.fitting_tf as ftf

        for section in self.k_series.split(gap_size=0).values():
            s, e = section.cov.start, section.cov.end  # inclusive, exclusive
            interval = (s, e)

            indices = np.searchsorted(initial_result['r_number'], section.cov.r_number)
            if not len(indices) == len(np.unique(indices)):
                raise ValueError('Invalid match between section r number and initial result r number')
            init_rate = initial_result['rate'][indices]

            regularizer = ftf.L1L2Differential(l1, l2)
            if k_int is not None:
                indices = np.searchsorted(k_int['r_number'], section.cov.r_number)
                if not len(indices) == len(np.unique(indices)):
                    raise ValueError('Invalid match between section r number and k_int r number')
                k_int_section = k_int['k_int'][indices]

                p_guess = k_int_section / init_rate
                guess_vals = np.log10(p_guess)

                parameter = ftf.TFParameter('log_P', (len(init_rate), 1), regularizer=regularizer)
                func = ftf.AssociationPFactFunc(section.timepoints)

                # expand dimensions of k_int to allow outer product with time and match the shape of parameter
                inputs_list = [section.cov.X, np.expand_dims(k_int_section, -1)]

            else:
                parameter = ftf.TFParameter('log_k', (len(init_rate), 1), regularizer=regularizer)
                func = ftf.AssociationRateFunc(section.timepoints)

                guess_vals = np.log10(init_rate)
                inputs_list = [section.cov.X]

            input_layers = [ftf.Input(array.shape) for array in inputs_list]
            layer = ftf.CurveFit([parameter], func, name='association')
            outputs = layer(input_layers)

            wts = np.expand_dims(guess_vals.astype(np.float32), -1)
            layer.set_weights([wts])

            model = ftf.Model(inputs=input_layers, outputs=outputs)

            input_data = [np.expand_dims(array, 0) for array in inputs_list]       # np.expand_dims(section.cov.X, 0)
            output_data = np.expand_dims(section.scores_peptides.T, 0)

            yield model, interval, input_data, output_data


class KineticsFitResult(object):
    """
    this fit results is only for wt avg fitting
    """
    def __init__(self, series, intervals, results, models):
        """
        each model with corresponding interval covers a region in the protein corresponding to r_number
        """
        assert len(results) == len(models)
#        assert len(models) == len(block_length)
        self.series = series
        self.r_number = series.cov.r_number
        self.intervals = intervals  #inclusive, excluive
        self.results = results
        self.models = models

    @property
    def model_type(self):
        if np.all([isinstance(m, SingleKineticModel) for m in self.models]):
            return 'Single'
        elif np.all([isinstance(m, LSQKinetics) for m in self.models]):
            return 'Global'
        else:
            return 'Mixed'

    def __call__(self, timepoints):
        """call the result with timepoints to get fitted uptake per peptide back"""
        d_list = []
        if self.model_type == 'Single':
            for time in timepoints:
                p = self.get_p(time)
                p = np.nan_to_num(p)
                d = self.series.cov.X.dot(p)
                d_list.append(d)
        elif self.model_type == 'Global':
            for time in timepoints:
                d = self.get_d(time)
                d_list.append(d)

        uptake = np.vstack(d_list).T
        return uptake

    def get_p(self, t):
        """
        Calculate P at timepoint t. Only for wt average type fitting results

        """
        p = np.full_like(self.r_number, fill_value=np.nan, dtype=float)
        for (s, e), result, model in zip(self.intervals, self.results, self.models):
            i0, i1 = np.searchsorted(self.r_number, [s, e])
            p[i0:i1] = model(t, **result.params)

        return p

    def get_d(self, t):
        "calculate d at timepoint t only for lsqkinetics (refactor glocal) type fitting results (scores per peptide)"
        d_arr = np.array([])
        for result, model in zip(self.results, self.models):
            d = np.array(model(t, **result.params)).flatten()
            d_arr = np.append(d_arr, d)
        return d_arr

    def __len__(self):
        return len(self.results)

    def __getitem__(self, item):
        raise DeprecationWarning()
        if isinstance(item, int):
            return self.results[item], self.models[item], self.block_length[item]
        else:
            return KineticsFitResult(self.results[item], self.models[item], self.block_length[item])

    def __iter__(self):
        raise DeprecationWarning()
        iterable = [(r, m, b) for r, m, b in zip(self.results, self.models, self.block_length)]
        return iterable.__iter__()

    def get_param(self, name):
        """
        Get an array of parameter with name `name` from the fit result. The length of the array is equal to the
        number of amino acids.

        Parameters
        ----------
        name : :obj:`str`
            Name of the parameter to extract

        Returns
        -------
        par_arr : :class:`~numpy.ndarray`
            Array with parameter values

        """

        output = np.full_like(self.r_number, np.nan, dtype=float)
        for (s, e), result, model in zip(self.intervals, self.results, self.models):
            try:
                dummy_name = model.names[name]  ## dummy parameter name
                value = result.params[dummy_name]  #value is scalar
            except KeyError:   # is a lsqmodel funky town
                value = model.get_param_values(name, **result.params)  #value is vector
                #values = model.get_parameter(name)            #todo unify nomenclature param/parameter

            i0, i1 = np.searchsorted(self.r_number, [s, e])
            output[i0:i1] = value

        return output

    @property
    def rate(self):
        """Returns an array with the exchange rates"""
        output = np.full_like(self.r_number, np.nan, dtype=float)
        for (s, e), result, model in zip(self.intervals, self.results, self.models):
            rate = model.get_rate(**result.params)
            i0, i1 = np.searchsorted(self.r_number, [s, e])
            output[i0:i1] = rate
        return output

    @property
    def tau(self):
        """Returns an array with the exchange rates"""
        return 1 / self.rate

        # output = np.full_like(self.r_number, np.nan, dtype=float)
        # for (s, e), result, model in zip(self.intervals, self.results, self.models):
        #     rate = model.get_rate(**result.params)
        #     i0, i1 = np.searchsorted(self.r_number, [s, e])
        #     output[i0:i1] = rate
        # return output

    def get_output(self, names):
        # change to property which gives all parameters as output
        dtype = [('r_number', int)] + [(name, float) for name in names]
        array = np.full_like(self.r_number, np.nan, dtype=dtype)
        array['r_number'] = self.r_number
        for name in names:
            try:
                array[name] = getattr(self, name)
            except AttributeError:
                array[name] = self.get_param(name)
        return array

    @property
    def output(self):
        return self.get_output(['rate', 'k1', 'k2', 'r'])


class LSQKinetics(KineticsModel): #TODO find a better name (lstsq)
    #todo block length is redundant
    #what is keeping it as attribute?
    #not really because its now needed to keep as we're not storing it anymore on FitREsults
    def __init__(self, initial_result, k_series, blocks, bounds, model_type='association'):
    #todo allow for setting of min/max value of parameters
        """

        Parameters
        ----------
        initial_result array with r_number and rate
        k_series kineticsseries object for the section
        """
        super(LSQKinetics, self).__init__(bounds)
        t_var = self.make_variable('t')

        self.block_length = blocks
        # self.model = model_type
        terms = []

        r_number = initial_result['r_number']
        r_index = k_series.cov.start
        for i, bl in enumerate(blocks):
            current = r_index + (bl // 2)
            idx = np.searchsorted(r_number, current)
            # if model == 'mono' or (model == 'mixed' and bl == 1):
            #     print('mono')
            #     #TODO update names
            #     print("this probably gives errors down the line")
            #     value = initial_result['rate'][idx]  #this should be the average of the block range
            #     r_index += bl
            #     k1 = self.make_parameter('k1_{}'.format(i), value=value)
            #     term = (1 - exp(-k1*t_var))
            #     terms.append(term)
            # else:
            #     # t1v = initial_result['tau1'][idx]  #TODO update names
                # t2v = initial_result['tau2'][idx]
            r_index += bl

            k1v = min(initial_result['k1'][idx], initial_result['k2'][idx])
            k2v = max(initial_result['k2'][idx], initial_result['k2'][idx])
            rv = np.clip(initial_result['r'][idx], 0.1, 0.9)
            k1 = self.make_parameter('k1_{}'.format(i), value=k1v)
            k2 = self.make_parameter('k2_{}'.format(i), value=k2v)
            r = self.make_parameter('r_{}'.format(i), max=1, min=0, value=rv)

            if model_type == 'association':
                term = (1 - (r * exp(-k1*t_var) + (1 - r) * exp(-k2*t_var)))
            elif model_type == 'dissociation':
                term = r * exp(-k1 * t_var) + (1 - r) * exp(-k2 * t_var)
            else:
                raise ValueError('Invalid choice of model: {}'.format(model_type))
            terms.append(term)

        #Iterate over rows (peptides) and add terms together which make one peptide
        model_dict = {}
        d_vars = []
        cs = np.insert(np.cumsum(blocks) + k_series.cov.start, 0, k_series.cov.start)
        for i, entry in enumerate(k_series.cov.data):
            d_var = self.make_variable('d_{}'.format(i))
            d_vars.append(d_var)

            #TODO THIS IS WRONG BECAUSE IT DOES NOT TAKE PROLINES INTO ACCOUNT
            s, e = entry['start'], entry['end']
            length = e - s + 1
            used_blocks = np.diff(np.clip((cs - s).copy(), a_min=0, a_max=length))
            fractions = used_blocks / length

            rhs = reduce(add, [100*fraction*term for fraction, term in zip(fractions, terms)])
            model_dict[d_var] = rhs

        self.d_vars = d_vars
        self.t_var = t_var
        self.sf_model = CallableModel(model_dict)

    def __call__(self, t, **params):
        """returns the callled model at time t for params, returns uptake values of peptides"""
        time_var = self.names['t']
        params[time_var] = t
        return self.sf_model(**params)

    def get_param_values(self, name, **params):
        """returns a list of parameters with name name which should have been indexed parameters
        params repeat during blocks

        """

        values = [params[self.names[f'{name}_{i}']] for i, _ in enumerate(self.block_length)]
        return np.repeat(values, self.block_length)

    def get_rate(self, **params):
        """

        Parameters
        ----------
        params

        key value where keys are the dummy names

        Returns
        -------

        """

        k_list = []
        for i, bl in enumerate(self.block_length):
            # if self.model == 'mono' or (self.model == 'mixed' and bl == 1):
            #     k = params[self.names['k1_{}'.format(i)]]
            #else:
            k1 = params[self.names['k1_{}'.format(i)]]
            k2 = params[self.names['k2_{}'.format(i)]]
            r = params[self.names['r_{}'.format(i)]]
            #todo this code is duplicated in TwoComponentAssociationModel
            x0 = k1 if r > 0.5 else k2

            # log of the time at which d-uptake is 50% for given k1, k2, and r
            t_log = fsolve(self.min_func, np.log(x0), args=(k1, k2, r))
            try:
                assert np.round(self.min_func(t_log, k1, k2, r), 5) == 0, 'Failed to find half life root'
            except AssertionError:
                print('uhoh')
                print(k1, k2, r)
                print(np.round(self.min_func(t_log, k1, k2, r), 5) == 0)
            k = np.asscalar(np.log(2) / np.exp(t_log))

                #tau = r * tau1 + (1 - r) * tau2
            k_list += [k] * bl

        return np.array(k_list)

    def get_tau(self, **params):
        """

        Parameters
        ----------
        params

        key value where keys are the dummy names

        Returns
        -------

        """
        k = self.get_rate(**params)
        return 1/k

    @staticmethod
    def min_func(t_log, k1, k2, r):
        t = np.exp(t_log)
        return 0.5 - r * np.exp(-k1*t) - (1 - r) * np.exp(-k2*t)




