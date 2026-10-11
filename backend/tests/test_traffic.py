from unittest.mock import patch

from app.services.store import Store
from app.services.traffic import TrafficSampler


class Metrics:
    def __init__(self):
        self.calls = 0
        self.server = 'server-one'
        self.target = 'node-one'
        self.peers = [{'public_key': 'peer', 'rx': 100, 'tx': 200, 'handshake': 1}]
        self.fail = False

    def describe(self):
        return {'driver': 'ssh', 'target': self.target, 'iface': 'wg0'}

    def run_agent(self, args):
        self.calls += 1
        if self.fail:
            raise RuntimeError('offline')
        return {'interface': 'wg0', 'public_key': self.server, 'peers': self.peers}


def make(tmp_path, interval=5):
    metrics = Metrics()
    store = Store(tmp_path / 'traffic.db')
    return metrics, store, TrafficSampler(store, lambda: metrics, interval)


def sample(sampler, timestamp):
    with patch('app.services.traffic.time.time', return_value=timestamp):
        return sampler.sample_once()


def test_reads_are_cache_only_and_disabled_sampler_never_calls_agent(tmp_path):
    metrics, _, sampler = make(tmp_path, interval=0)
    sampler.start()
    sampler.snapshot()
    sampler.series(60)
    assert metrics.calls == 0
    assert not sampler.sample_once()


def test_totals_survive_restart_and_counter_reset(tmp_path):
    metrics, store, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.peers[0].update(rx=150, tx=260)
    sample(sampler, 1005)
    assert sampler.snapshot()['rx'] == 150
    sampler = TrafficSampler(store, lambda: metrics, 5)
    metrics.peers[0].update(rx=180, tx=280)
    sample(sampler, 1010)
    assert sampler.snapshot()['rx'] == 180
    metrics.peers[0].update(rx=2, tx=3)
    sample(sampler, 1015)
    assert sampler.snapshot()['rx'] == 180
    assert sampler.snapshot()['up_rate'] == 0
    metrics.peers[0].update(rx=12, tx=23)
    sample(sampler, 1020)
    assert sampler.snapshot()['rx'] == 190
    assert sampler.snapshot()['down_rate'] == 4
    assert metrics.calls == 5


def test_missing_peer_clears_rates_but_preserves_totals(tmp_path):
    metrics, _, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.peers = []
    sample(sampler, 1005)
    result = sampler.snapshot()
    assert result['peers'] == []
    assert result['rx'] == 100
    assert result['up_rate'] == 0


def test_gap_is_null_and_recovery_does_not_spread_offline_bytes(tmp_path):
    metrics, _, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.fail = True
    sample(sampler, 1010)
    assert sampler.snapshot()['available'] is False
    assert sampler.snapshot()['up_rate'] is None
    metrics.fail = False
    metrics.peers[0]['rx'] = 1000
    sample(sampler, 1030)
    with patch('app.services.traffic.time.time', return_value=1030):
        series = sampler.series(60)
    assert any(point[1] is None for point in series['points'])
    assert not any((point[1] or 0) > 0 for point in series['points'])
    assert sampler.snapshot()['rx'] == 1000


def test_server_key_and_connection_isolate_history(tmp_path):
    metrics, _, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.server = 'server-two'
    metrics.peers[0]['rx'] = 7
    sample(sampler, 1005)
    assert sampler.snapshot()['rx'] == 7
    metrics.target = 'node-two'
    assert sampler.snapshot()['peers'] == []
    assert sampler.snapshot()['rx'] == 0
    sample(sampler, 1010)
    assert sampler.snapshot()['rx'] == 7


def test_rollups_replace_fine_data_without_double_counting(tmp_path):
    metrics, store, sampler = make(tmp_path)
    for ts in range(1000, 1401, 5):
        metrics.peers[0]['rx'] = ts
        sample(sampler, ts)
    with store._traffic_connection() as conn:
        assert conn.execute('SELECT MIN(ts) FROM traffic_samples').fetchone()[0] >= 1220
        assert conn.execute('SELECT COUNT(*) FROM traffic_rollups WHERE resolution=60').fetchone()[0] > 0
    with patch('app.services.traffic.time.time', return_value=1400):
        points = sampler.series(3600)['points']
    assert abs(sum((p[1] or 0) * 60 for p in points) - 400) < 0.001


def test_normal_sampling_fills_observed_interval(tmp_path):
    metrics, _, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.peers[0]['rx'] = 150
    sample(sampler, 1005)
    with patch('app.services.traffic.time.time', return_value=1005):
        points = sampler.series(60)['points']
    assert [p[1] for p in points if 1000 <= p[0] < 1005] == [10] * 5


def test_rollups_age_to_hours_and_expire(tmp_path):
    metrics, store, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.peers[0]['rx'] = 150
    sample(sampler, 1005)
    store.compact_traffic(100000)
    with store._traffic_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM traffic_samples').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM traffic_rollups WHERE resolution=60').fetchone()[0] == 0
        assert conn.execute('SELECT SUM(rx_delta) FROM traffic_rollups WHERE resolution=3600').fetchone()[0] == 50
    store.compact_traffic(16 * 86400 + 2000)
    with store._traffic_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM traffic_rollups').fetchone()[0] == 0
    assert sampler.snapshot()['rx'] == 150


def test_disabled_sampler_can_read_saved_history_after_restart(tmp_path):
    metrics, store, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.peers[0]['rx'] = 150
    sample(sampler, 1005)
    reader = TrafficSampler(store, lambda: metrics, 0)
    assert reader.snapshot()['rx'] == 150
    with patch('app.services.traffic.time.time', return_value=1005):
        assert any(p[1] is not None for p in reader.series(60)['points'])
    assert metrics.calls == 2


def test_one_counter_reset_does_not_lose_other_direction(tmp_path):
    metrics, _, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.peers[0].update(rx=2, tx=220)
    sample(sampler, 1005)
    result = sampler.snapshot()
    assert result['rx'] == 100
    assert result['tx'] == 220
    assert result['up_rate'] == 0
    assert result['down_rate'] == 4


def test_scope_uses_peer_public_key(tmp_path):
    metrics, _, sampler = make(tmp_path)
    metrics.peers.append({'public_key': 'another', 'rx': 0, 'tx': 0})
    sample(sampler, 1000)
    metrics.peers[0]['rx'] = 150
    metrics.peers[1]['rx'] = 250
    sample(sampler, 1005)
    with patch('app.services.traffic.time.time', return_value=1005):
        scoped = sampler.series(60, 'peer')
    assert sum(p[1] or 0 for p in scoped['points']) == 50


def test_peer_reappearing_has_unknown_rate_across_absence(tmp_path):
    metrics, _, sampler = make(tmp_path)
    sample(sampler, 1000)
    metrics.peers = []
    sample(sampler, 1005)
    metrics.peers = [{'public_key': 'peer', 'rx': 500, 'tx': 300}]
    sample(sampler, 1010)
    assert sampler.snapshot()['peers'][0]['rx_rate'] is None
    assert sampler.snapshot()['rx'] == 500
