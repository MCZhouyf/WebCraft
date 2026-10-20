from repro.public_observation_scoring import boundary_readout


def record(intent='commit', error=None):
    return {'used_mechanism_ids':['old'], 'events':[{
        'method':'craft_grid','intent':intent,'success':False,
        'params':{'layout':['stick']},'item_flow':{'produced':[],'consumed':[]},
        'result':{'success':False,**({'error':error} if error else {})}}]}


def test_every_policy_can_read_a_public_rejection():
    assertions={'old':{'kind':'recipe','output':'widget','count':1}}
    assert boundary_readout(record(),assertions,{'old':True})=={'old':False}
    assert boundary_readout(record(),assertions,{'old':True},probe_only=True)=={'old':True}


def test_missing_resources_do_not_refute_a_recipe():
    assertions={'old':{'kind':'recipe','output':'widget','count':1}}
    assert boundary_readout(record(error='missing ingredient'),assertions,{'old':True})=={'old':True}


def test_ambiguous_execution_error_retains_the_prior_disposition():
    assertions={'old':{'kind':'recipe','output':'widget','count':1}}
    assert boundary_readout(record(error='action could not be completed'),assertions,{'old':True})=={'old':True}
