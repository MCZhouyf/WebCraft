from arbiter.inference import ContextMixtureModel, ProbeEvidence, SourceEvidence


def test_historical_tense_is_independent_source():
    sources = [
        SourceEvidence("wiki", {"m": 0}, timestamp=0, tense="historical"),
        SourceEvidence("wiki", {"m": 1}, timestamp=100, tense="current"),
    ]
    model = ContextMixtureModel(max_k=1).fit(sources)
    assert set(model.source_posteriors()) == {"wiki::historical", "wiki::current"}


def test_probe_changes_world_posterior():
    sources = [
        SourceEvidence("a", {"m": 0, "x": 0}, timestamp=0),
        SourceEvidence("b", {"m": 0, "x": 0}, timestamp=1),
        SourceEvidence("c", {"m": 1, "x": 1}, timestamp=100),
        SourceEvidence("d", {"m": 1, "x": 1}, timestamp=101),
    ]
    model = ContextMixtureModel(max_k=2).fit(sources, [ProbeEvidence("m", 0)])
    assert model.k_ == 2
    assert model.world_responsibilities_[0] > 0.8


def test_time_prior_ablation_is_available():
    sources = [
        SourceEvidence("a", {"m": 0}, timestamp=0),
        SourceEvidence("b", {"m": 1}, timestamp=100),
    ]
    model = ContextMixtureModel(max_k=1, use_time_prior=False).fit(sources)
    assert model.k_ == 1
