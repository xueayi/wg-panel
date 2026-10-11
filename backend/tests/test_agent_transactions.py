import concurrent.futures
import json
import shutil


def test_concurrent_add_keeps_unique_ips_and_all_clients(wg):
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda i: wg['run']('add', f'client-{i}'), range(8)))
    assert len({r['ip'] for r in rows}) == 8
    assert len(wg['registry']()['clients']) == 8


def test_failed_rename_restores_all_client_files(wg):
    wg['run']('add', 'alpha')
    b = wg['run']('add', 'beta')
    before = {p.name: p.read_bytes() for p in wg['clients_dir'].iterdir()}
    wg['run']('update', 'alpha', '--new-name', 'renamed', '--ip', b['ip'], expect_ok=False)
    assert {p.name: p.read_bytes() for p in wg['clients_dir'].iterdir()} == before


def test_sync_failure_restores_client_files(wg):
    wg['run']('add', 'alpha')
    before = {p.name: p.read_bytes() for p in wg['clients_dir'].iterdir()}
    wg['fake_wg'].write_text(wg['fake_wg'].read_text().replace('elif a[0] == "syncconf":', 'elif a[0] == "syncconf":\n    sys.exit(1)'))
    wg['run']('update', 'alpha', '--new-name', 'renamed', expect_ok=False)
    assert {p.name: p.read_bytes() for p in wg['clients_dir'].iterdir()} == before


def test_resign_keeps_preshared_key(wg):
    wg['run']('add', 'alpha')
    reg = wg['registry'](); reg['clients']['alpha']['preshared_key'] = 'PSK_SECRET'
    (wg['base'] / 'registry.json').write_text(json.dumps(reg))
    conf = wg['conf_text']().replace('[Peer]', '[Peer]\nPresharedKey = PSK_SECRET')
    (wg['wg_dir'] / 'wg0.conf').write_text(conf)
    wg['run']('adopt', '--yes')
    wg['run']('update', 'alpha', '--note', 'unchanged')
    assert 'PresharedKey = PSK_SECRET' in wg['client_conf']('alpha')


def test_adopt_rejects_unsupported_directives_without_writes(wg):
    conf = wg['conf_text']() + '\nTable = off\nPreUp = echo required\n'
    (wg['wg_dir'] / 'wg0.conf').write_text(conf)
    wg['run']('adopt', '--yes', expect_ok=False)
    assert wg['conf_text']() == conf


def test_write_rejects_external_config_drift(wg):
    wg['run']('add', 'alpha')
    conf = wg['conf_text']().replace('iptables -A FORWARD', 'echo external; iptables -A FORWARD')
    (wg['wg_dir'] / 'wg0.conf').write_text(conf)
    wg['run']('update', 'alpha', '--note', 'new', expect_ok=False)
    assert wg['conf_text']() == conf
    assert wg['registry']()['clients']['alpha']['note'] == ''


def test_plan_request_validates_custom_and_binds_params(wg):
    request = {'operation': 'add_peer', 'name': 'custom', 'params': {'tunnel': 'custom', 'allowed_ips': '192.168.100.0/24', 'note': 'hello'}}
    before = wg['registry']()
    plan = wg['run']('plan', '--request', json.dumps(request))
    assert plan['revision']
    assert wg['registry']() == before
    changed = dict(request, params={**request['params'], 'note': 'changed'})
    wg['run']('apply', '--request', json.dumps(changed), '--revision', plan['revision'], expect_ok=False)
    assert 'custom' not in wg['registry']()['clients']
    wg['run']('apply', '--request', json.dumps(request), '--revision', plan['revision'])
    assert wg['registry']()['clients']['custom']['client_allowed_ips'] == ['192.168.100.0/24']


def test_old_plan_rejects_endpoint_change(wg):
    wg['run']('add', 'alpha')
    req = {'operation': 'update_peer', 'name': 'alpha', 'params': {'note': 'preview'}}
    plan = wg['run']('plan', '--request', json.dumps(req))
    wg['run']('update', 'alpha', '--peer-endpoint', '203.0.113.10:51820')
    wg['run']('apply', '--request', json.dumps(req), '--revision', plan['revision'], expect_ok=False)
    assert wg['registry']()['clients']['alpha']['note'] == ''


def test_sync_success_without_runtime_change_is_rolled_back(wg):
    wg['run']('add', 'alpha')
    before = wg['registry']()
    wg['fake_wg'].write_text(wg['fake_wg'].read_text().replace('elif a[0] == "syncconf":', 'elif a[0] == "syncconf":\n    sys.exit(0)'))
    wg['run']('add', 'beta', expect_ok=False)
    assert wg['registry']() == before
    assert not (wg['clients_dir'] / 'beta.key').exists()


def test_new_network_adds_and_removes_system_address(wg):
    wg['run']('net-add', '10.8.2.0/24')
    log = (wg['base'] / 'ip-commands.json').read_text()
    assert '10.8.2.1/24' in log and 'add' in log
    wg['run']('net-rm', '10.8.2.0/24')
    assert 'del' in (wg['base'] / 'ip-commands.json').read_text()


def test_site_route_is_added_and_reclaimed(wg):
    wg['run']('add', 'gateway', '--site-routes', '192.168.50.0/24')
    log = (wg['base'] / 'ip-commands.json').read_text()
    assert '192.168.50.0/24' in log and 'add' in log
    wg['run']('remove', 'gateway', '--yes')
    assert 'del' in (wg['base'] / 'ip-commands.json').read_text()


def test_adopt_imports_linuxserver_client_private_key_and_psk(wg):
    wg['run']('add', 'phone')
    client = wg['client_conf']('phone') + 'PresharedKey = PSK_SECRET\n'
    conf = wg['conf_text']().replace('[Peer]', '[Peer]\nPresharedKey = PSK_SECRET')
    (wg['wg_dir'] / 'wg0.conf').write_text(conf)
    peer_dir = wg['wg_dir'].parent / 'peer_phone'; peer_dir.mkdir()
    (peer_dir / 'peer_phone.conf').write_text(client)
    for f in wg['clients_dir'].iterdir(): f.unlink()
    (wg['base'] / 'registry.json').unlink()
    wg['run']('adopt', '--yes')
    rows = wg['run']('list')['rows']
    managed = next(r for r in rows if r['pubkey'])
    assert managed['exportable']
    assert (wg['clients_dir'] / (managed['name']+'.key')).exists()
    wg['run']('update', managed['name'], '--note', 'keep-psk')
    assert 'PresharedKey = PSK_SECRET' in wg['client_conf'](managed['name'])


def test_adopt_json_preview_does_not_write(wg):
    wg['run']('add', 'phone')
    before = wg['registry']()
    preview = wg['run']('adopt', '--dry-run')
    assert preview['adopted'] == 1
    assert preview['revision']
    assert wg['registry']() == before


def _stage_interrupted_transaction(wg):
    """模拟一次异常中断：事务快照 + JOURNAL 已落盘，但现场文件被改乱。"""
    folder = wg['base'] / 'backup' / 'transaction-interrupted'
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wg['wg_dir'] / 'wg0.conf', folder / 'server.conf')
    shutil.copy2(wg['base'] / 'registry.json', folder / 'registry.json')
    shutil.copytree(wg['clients_dir'], folder / 'clients')
    (folder / 'manifest.json').write_text(json.dumps(
        {"server.conf": True, "registry.json": True, "clients": True}))
    (wg['base'] / 'transaction.json').write_text(json.dumps({"backup": str(folder)}))
    # 把现场改乱：registry 写坏 + conf 写坏，模拟写到一半崩掉
    (wg['base'] / 'registry.json').write_text('{not json')
    (wg['wg_dir'] / 'wg0.conf').write_text('garbage\n')


def test_interrupted_transaction_recovers_on_next_write(wg):
    wg['run']('add', 'alpha')
    before_files = {p.name: p.read_bytes() for p in wg['clients_dir'].iterdir()}
    _stage_interrupted_transaction(wg)
    # 只读命令不触发恢复，也不会被坏 registry 打崩之外的副作用影响
    out = wg['run']('update', 'alpha', '--note', 'after-recovery')
    assert out['name'] == 'alpha'
    assert wg['registry']()['clients']['alpha']['note'] == 'after-recovery'
    assert {p.name: p.read_bytes() for p in wg['clients_dir'].iterdir()} == before_files
    assert not (wg['base'] / 'transaction.json').exists()


def test_failed_recovery_keeps_journal_for_retry(wg):
    original = wg['fake_wg'].read_text()
    wg['run']('add', 'alpha')
    wg['fake_wg'].write_text(original.replace(
        'elif a[0] == "syncconf":', 'elif a[0] == "syncconf":\n    sys.exit(1)'))
    # peer-keepalive 会渲染进服务端配置：conf 已改写、syncconf 失败 → 恢复不完整
    wg['run']('update', 'alpha', '--peer-keepalive', '30', expect_ok=False)
    # 恢复不完整（syncconf 持续失败）：JOURNAL 必须保留，undo 队列不能悄悄丢掉
    assert (wg['base'] / 'transaction.json').exists()
    wg['fake_wg'].write_text(original)
    # 修好环境后，下条写命令先完成恢复（恢复文件 + 重新同步旧配置），再执行新变更
    out = wg['run']('update', 'alpha', '--note', 'retry')
    assert out['name'] == 'alpha'
    assert wg['registry']()['clients']['alpha']['note'] == 'retry'
    assert not (wg['base'] / 'transaction.json').exists()


def test_rollback_replay_skips_already_applied_inverse_ops(wg):
    """undo 队列重放时，已生效的逆操作要跳过，不能反复执行卡死恢复。"""
    wg['run']('add', 'gateway', '--site-routes', '192.168.50.0/24')
    log = json.loads((wg['base'] / 'ip-commands.json').read_text())
    ip_bin = next(a for a in log if 'route' in a and 'add' in a)[0]
    _stage_interrupted_transaction(wg)
    journal_path = wg['base'] / 'transaction.json'
    journal = json.loads(journal_path.read_text())
    # 塞进一条「已生效」的逆操作：fake ip 对 -j 查询返回空 → 目标已不存在 → 应跳过
    journal.setdefault('undo', []).append([ip_bin, 'route', 'del', '192.168.50.0/24', 'dev', 'wg0'])
    journal_path.write_text(json.dumps(journal))
    wg['run']('update', 'gateway', '--note', 'after-recovery')
    assert not journal_path.exists()
    assert wg['registry']()['clients']['gateway']['note'] == 'after-recovery'


def test_restore_reports_registry_mismatch(wg):
    wg['run']('add', 'alpha')
    conf_backup = wg['conf_text']()
    reg = wg['registry']()
    reg['clients']['alpha']['allowed_ips'] = ['10.8.1.99/32']
    (wg['base'] / 'registry.json').write_text(json.dumps(reg))
    bfile = wg['base'] / 'backup' / 'wg0.conf.manual-test'
    bfile.write_text(conf_backup)
    out = wg['run']('restore', 'wg0.conf.manual-test', '--yes')
    assert out['restored'] == 'wg0.conf.manual-test'
    assert out['registry_warning'], '恢复旧配置与登记表不一致时必须提示'
    assert wg['conf_text']() == conf_backup
