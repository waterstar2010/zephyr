
'''
Discretization base classes for Zephyr
'''

import copy
import numpy as np
import scipy.sparse as sp
from .meta import AttributeMapper, BaseSCCache, BaseModelDependent
from .solver import DirectSolver

try:
    from multiprocessing import Pool, Process
except ImportError:
    PARALLEL = False
else:
    PARALLEL = True

PARTASK_TIMEOUT = 60

class BaseDiscretization(BaseModelDependent):
    '''
    Base class for all discretizations.
    '''
    
    initMap = {
    #   Argument        Required    Rename as ...   Store as type
        'c':            (True,      '_c',           np.complex128),
        'rho':          (False,     '_rho',         np.float64),
        'freq':         (True,      None,           np.complex128),
        'Solver':       (False,     '_Solver',      None),
    }
    
    @property
    def c(self):
        'Complex wave velocity'
        if isinstance(self._c, np.ndarray):
            return self._c
        else:
            return self._c * np.ones((self.nz, self.nx), dtype=np.complex128)
    
    @property
    def rho(self):
        'Bulk density'
        if getattr(self, '_rho', None) is None:
            self._rho = 310. * self.c**0.25
            
        if isinstance(self._rho, np.ndarray):
            return self._rho
        else:
            return self._rho * np.ones((self.nz, self.nx), dtype=np.float64)
    
    @property
    def shape(self):
        return self.A.T.shape
    
    @property
    def Ainv(self):
        'Instance of a Solver class that implements forward modelling'
        
        if not hasattr(self, '_Ainv'):
            self._Ainv = DirectSolver(getattr(self, '_Solver', None))
            self._Ainv.A = self.A.tocsc()
        return self._Ainv
    
    def __mul__(self, rhs):
        'Action of multiplying the inverted system by a right-hand side'
        return self.Ainv * rhs
    
    def __call__(self, value):
        return self*value


class DiscretizationWrapper(BaseSCCache):
    '''
    Base class for objects that wrap around discretizations, for example
    in order to model multiple subproblems and distribute configurations
    to different systems.
    '''
    
    initMap = {
    #   Argument        Required    Rename as ...   Store as type
        'disc':         (True,      None,           None),
        'scaleTerm':    (False,     '_scaleTerm',   np.complex128),
    }
    
    cacheItems = ['_subProblems']
    
    @property
    def scaleTerm(self):
        'A scaling term to apply to the output wavefield.'
        
        return getattr(self, '_scaleTerm', 1.)
    
    @property
    def _spConfigs(self):
        '''
        Returns subProblem configurations based on the stored
        systemConfig and any subProblem updates.
        '''
        
        def duplicateUpdate(spu):
            nsc = copy.copy(self.systemConfig)
            nsc.update(spu)
            return nsc
        
        return (duplicateUpdate(spu) for spu in self.spUpdates)
    
    @property
    def subProblems(self):
        'Returns subProblem instances based on the discretization.'
        
        if getattr(self, '_subProblems', None) is None:
            
            self._subProblems = map(self.disc, self._spConfigs)
        return self._subProblems
    
    @property
    def spUpdates(self):
        raise NotImplementedError
    
    def __mul__(self, rhs):
        raise NotImplementedError


class MultiFreq(DiscretizationWrapper):
    '''
    Wrapper to carry out forward-modelling using the stored
    discretization over a series of frequencies.
    '''
    
    initMap = {
    #   Argument        Required    Rename as ...   Store as type
        'disc':         (True,      '_disc',        None),
        'freqs':        (True,      None,           list),
        'parallel':     (False,     '_parallel',    bool),
    }
    
    maskKeys = ['disc', 'freqs', 'parallel']
    
    @property
    def parallel(self):
        'Determines whether to operate in parallel'
        
        return PARALLEL and getattr(self, '_parallel', True)
    
    @property
    def spUpdates(self):
        'Updates for frequency subProblems'
        
        return [{'freq': freq} for freq in self.freqs]
    
    @property
    def disc(self):
        'The discretization to instantiate'
        
        return self._disc

    def __mul__(self, rhs):
        '''
        Carries out the multiplication of the composite system
        by the right-hand-side vector(s).
        
        Args:
            rhs (array-like or list thereof): Source vectors
        
        Returns:
            u (iterator over np.ndarrays): Wavefields
        '''
        
        if isinstance(rhs, list):
            getRHS = lambda i: rhs[i]
        else:
            getRHS = lambda i: rhs
        
        if self.parallel:
            pool = Pool()
            plist = []
            for i, sp in enumerate(self.subProblems):
                
                p = pool.apply_async(sp, (getRHS(i),))
                plist.append(p)
            
            u = (self.scaleTerm*p.get(PARTASK_TIMEOUT) for p in plist)
            pool.close()
            pool.join()
        else:
            u = (self.scaleTerm*(sp*getRHS(i)) for i, sp in enumerate(self.subProblems))
        
        return u
