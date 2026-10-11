import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { mkdtemp, rm } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifacts = await mkdtemp(resolve(root, '.regression-'))
after(() => rm(artifacts, { recursive: true, force: true }))
async function bundle(name) {
  const outfile = resolve(artifacts, `${name}.mjs`)
  await build({ entryPoints: [resolve(root, `src/views/${name}.tsx`)], outfile, bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic' })
  return import(pathToFileURL(outfile).href)
}
const peers = await bundle('Peers')
const dashboard = await bundle('Dashboard')
const connection = await bundle('Connection')
const api = await import(pathToFileURL((await (async () => {
  const outfile = resolve(artifacts, 'api.mjs')
  await build({ entryPoints: [resolve(root, 'src/api.ts')], outfile, bundle: true, platform: 'node', format: 'esm', packages: 'external' })
  return outfile
})())).href)
const peer = { name: 'phone', endpoint: '203.0.113.7:45678', configured_endpoint: '', keepalive: 17, client_keepalive: 15, exportable: false, export_reason: '缺少客户端私钥', ip: '10.8.0.2', tunnel: 'lan' }

test('仅修改名称不会提交运行时 Endpoint 或重置 Keepalive', () => {
  assert.equal(typeof peers.peerEditChanges, 'function')
  assert.deepEqual(peers.peerEditChanges(peer, { name: 'tablet', endpoint: '', keepalive: '17', clientKeepalive: '15' }), { new_name: 'tablet' })
  assert.deepEqual(peers.peerEditChanges(peer, { name: 'phone', endpoint: '', keepalive: '17', clientKeepalive: '15' }), {})
  assert.deepEqual(peers.peerEditChanges(peer, { name: 'phone', endpoint: '', keepalive: '0', clientKeepalive: '0' }), { keepalive: 0, client_keepalive: 0 })
})
test('编辑使用登记表配置值，缺少私钥禁用下载和二维码', () => {
  assert.equal(typeof peers.ConfigModal, 'function')
  const html = renderToStaticMarkup(React.createElement(peers.ConfigModal, { peer, onClose() {}, onSaved() {}, onToast() {} }))
  assert.doesNotMatch(html, /203\.0\.113\.7/)
  assert.match(html, /value="17"/)
  assert.match(html, /value="15"/)
  assert.match(html, /缺少客户端私钥/)
  assert.doesNotMatch(html, /<img|href="\/api\/peers\/phone\/config/)
})
test('完整配置下载显式请求明文并正确编码名称', () => {
  assert.equal(api.api.configUrl('a b', true), '/api/peers/a%20b/config?reveal=true')
  assert.equal(typeof peers.ConfigModal, 'function')
  const html = renderToStaticMarkup(React.createElement(peers.ConfigModal, { peer: { ...peer, exportable: true }, onClose() {}, onSaved() {}, onToast() {} }))
  assert.match(html, /href="\/api\/peers\/phone\/config\?reveal=true"/)
})
test('流量折线图带坐标刻度，缺口断线并铺斜纹', () => {
  assert.equal(typeof dashboard.TrafficChart, 'function')
  const html = renderToStaticMarkup(React.createElement(dashboard.TrafficChart, { series: { window: 3600, step: 60, points: [[1, 1024, 2048], [2, null, null], [3, 0, 0]] } }))
  assert.match(html, /<path[^>]*d="M[^"]+"/)          // 上下行折线
  assert.match(html, /d="[^"]+M[0-9.]+/)              // 缺口处断线（一条 path 内重新起笔）
  assert.match(html, /url\(#chart-gap\)/)             // 缺口铺斜纹而非拉成 0
  assert.match(html, /KiB\/s/)                        // Y 轴速率刻度
  assert.match(html, /text-anchor="end"/)              // 刻度文字右对齐贴轴
  assert.match(html, /aria-label="[^"]*折线图[^"]*"/)
  assert.match(html, /数据缺口/)
})

test('管理模式作为启动配置只读展示', () => {
  const html = renderToStaticMarkup(React.createElement(connection.default, { nonce: 0, onToast() {} }))
  assert.match(html, /WGP_DRIVER/)
  assert.match(html, /重建容器/)
  assert.doesNotMatch(html, /<select/)
})

test('纳管先预览展示私钥缺失和警告，取消不写，确认提交相同revision', async () => {
  assert.equal(typeof api.confirmAdoption, 'function')
  const originalFetch = globalThis.fetch
  const calls = []
  const preview = { revision: 'snapshot1', adopted: 2, clients: [{ name: 'phone', public_key: 'pub1', exportable: true }, { name: 'router', public_key: 'pub2', exportable: false }], warnings: ['请保留已有 PSK'] }
  globalThis.fetch = async (url, init) => {
    calls.push({ url, method: init?.method || 'GET' })
    return new Response(JSON.stringify(url.endsWith('adopt-preview') ? preview : { adopted: 2 }))
  }
  try {
    assert.equal(await api.confirmAdoption((message) => {
      assert.match(message, /router.*缺少私钥/)
      assert.match(message, /请保留已有 PSK/)
      return false
    }), false)
    assert.deepEqual(calls, [{ url: '/api/adopt-preview', method: 'GET' }])
    calls.length = 0
    assert.equal(await api.confirmAdoption(() => true), true)
    assert.deepEqual(calls, [{ url: '/api/adopt-preview', method: 'GET' }, { url: '/api/adopt?confirm=true&revision=snapshot1', method: 'POST' }])
  } finally {
    globalThis.fetch = originalFetch
  }
})
