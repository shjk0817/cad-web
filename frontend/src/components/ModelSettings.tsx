import { useEffect, useMemo, useState } from 'react'
import {
  createLlmProvider,
  deleteLlmProvider,
  listLlmProviders,
  testLlmProvider,
  updateLlmProvider,
} from '../api'
import type {
  LLMProviderConfig,
  LLMProviderPreset,
  LLMProtocol,
} from '../types'

interface ModelSettingsProps {
  onToast: (tone: 'success' | 'warn' | 'error' | 'info', text: string) => void
}

// 模型设置面板：选择 preset + 增删改 + 联通性测试
export default function ModelSettings({ onToast }: ModelSettingsProps) {
  const [presets, setPresets] = useState<LLMProviderPreset[]>([])
  const [configs, setConfigs] = useState<LLMProviderConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<LLMProviderConfig | null>(null)
  const [creating, setCreating] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)

  const refresh = async () => {
    setLoading(true)
    try {
      const data = await listLlmProviders()
      setPresets(data.presets || [])
      setConfigs(data.configs || [])
    } catch (e: unknown) {
      onToast('error', (e as Error).message || '加载模型列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const handleCreate = async (cfg: Partial<LLMProviderConfig>) => {
    try {
      await createLlmProvider(cfg as Omit<LLMProviderConfig, 'id'>)
      onToast('success', `已添加「${cfg.name}」`)
      setCreating(false)
      await refresh()
    } catch (e: unknown) {
      onToast('error', (e as Error).message || '添加失败')
    }
  }

  const handleUpdate = async (id: string, patch: Partial<LLMProviderConfig>) => {
    try {
      await updateLlmProvider(id, patch)
      onToast('success', '已保存')
      setEditing(null)
      await refresh()
    } catch (e: unknown) {
      onToast('error', (e as Error).message || '保存失败')
    }
  }

  const handleDelete = async (cfg: LLMProviderConfig) => {
    if (!confirm(`确认删除「${cfg.name}」？`)) return
    try {
      await deleteLlmProvider(cfg.id)
      onToast('success', `已删除「${cfg.name}」`)
      await refresh()
    } catch (e: unknown) {
      onToast('error', (e as Error).message || '删除失败')
    }
  }

  const handleTest = async (cfg: LLMProviderConfig) => {
    setTesting(cfg.id)
    try {
      const res = await testLlmProvider(cfg.id)
      if (res.success) onToast('success', `「${cfg.name}」联通性正常`)
      else onToast('error', `「${cfg.name}」联通性失败：${res.error || ''}`)
    } catch (e: unknown) {
      onToast('error', (e as Error).message || '联通性测试失败')
    } finally {
      setTesting(null)
    }
  }

  return (
    <section className="model-settings">
      <header className="model-settings-header">
        <h2>模型设置</h2>
        <p className="model-settings-helper">
          配置用于工程量复核的国产 / OpenAI 兼容 LLM 与 VLM Provider；
          VLM 用于图纸识别，文本 LLM 用于工程量推理
        </p>
        <button
          type="button"
          className="tool-btn primary"
          onClick={() => setCreating(true)}
        >
          ＋ 新增 Provider
        </button>
      </header>

      {loading && <p className="model-settings-helper">加载中…</p>}

      {!loading && configs.length === 0 && !creating && (
        <p className="model-settings-empty">
          尚未配置任何 Provider；点击右上角「＋ 新增 Provider」开始
        </p>
      )}

      <ul className="model-settings-cards">
        {configs.map((cfg) => (
          <li key={cfg.id}>
            <ProviderCard
              cfg={cfg}
              presets={presets}
              testing={testing === cfg.id}
              onEdit={() => setEditing(cfg)}
              onDelete={() => handleDelete(cfg)}
              onTest={() => handleTest(cfg)}
              onUpdate={(patch) => handleUpdate(cfg.id, patch)}
            />
          </li>
        ))}
      </ul>

      {creating && (
        <ProviderForm
          presets={presets}
          onCancel={() => setCreating(false)}
          onSubmit={handleCreate}
        />
      )}

      {editing && (
        <ProviderForm
          presets={presets}
          initial={editing}
          onCancel={() => setEditing(null)}
          onSubmit={(patch) =>
            handleUpdate(editing.id, patch as Partial<LLMProviderConfig>)
          }
        />
      )}
    </section>
  )
}

interface ProviderCardProps {
  cfg: LLMProviderConfig
  presets: LLMProviderPreset[]
  testing: boolean
  onEdit: () => void
  onDelete: () => void
  onTest: () => void
  onUpdate: (patch: Partial<LLMProviderConfig>) => Promise<void>
}

function ProviderCard({
  cfg,
  presets,
  testing,
  onEdit,
  onDelete,
  onTest,
  onUpdate,
}: ProviderCardProps) {
  const preset = useMemo(
    () => presets.find((p) => p.id === cfg.presetId),
    [presets, cfg.presetId],
  )

  const handleToggle = async () => {
    await onUpdate({ enabled: !cfg.enabled })
  }

  return (
    <article className={`provider-card ${cfg.enabled ? 'enabled' : 'disabled'}`}>
      <header className="provider-card-header">
        <h3>{cfg.name}</h3>
        <span className="provider-protocol">{cfg.protocol}</span>
      </header>
      <p className="provider-meta">
        {preset?.name || cfg.presetId}
        {preset?.supportsVision === false && ' · 仅文本'}
      </p>
      <dl className="provider-fields">
        <dt>Base URL</dt>
        <dd>{cfg.baseUrl}</dd>
        <dt>Chat 模型</dt>
        <dd>{cfg.chatModel}</dd>
        <dt>Vision 模型</dt>
        <dd>{cfg.visionModel}</dd>
        <dt>API Key</dt>
        <dd>
          <code>{cfg.apiKey ? '••••••••' + cfg.apiKey.slice(-4) : '未填写'}</code>
        </dd>
      </dl>
      <div className="provider-actions">
        <label className="provider-toggle">
          <input
            type="checkbox"
            checked={cfg.enabled}
            onChange={handleToggle}
          />
          <span>{cfg.enabled ? '已启用' : '已禁用'}</span>
        </label>
        <button
          type="button"
          className="tool-btn"
          onClick={onTest}
          disabled={testing}
        >
          {testing ? '测试中…' : '联通性测试'}
        </button>
        <button
          type="button"
          className="tool-btn"
          onClick={onEdit}
        >
          编辑
        </button>
        <button
          type="button"
          className="tool-btn danger"
          onClick={onDelete}
        >
          删除
        </button>
      </div>
    </article>
  )
}

interface ProviderFormProps {
  presets: LLMProviderPreset[]
  initial?: Partial<LLMProviderConfig>
  onCancel: () => void
  onSubmit: (data: Partial<LLMProviderConfig>) => Promise<void>
}

const PROTOCOL_OPTIONS: LLMProtocol[] = ['openai', 'anthropic', 'gemini']

function ProviderForm({
  presets,
  initial,
  onCancel,
  onSubmit,
}: ProviderFormProps) {
  const [presetId, setPresetId] = useState<string>(
    initial?.presetId || presets[0]?.id || '',
  )
  const preset = useMemo(
    () => presets.find((p) => p.id === presetId),
    [presets, presetId],
  )
  const [name, setName] = useState(initial?.name || preset?.name || '')
  const [protocol, setProtocol] = useState<LLMProtocol>(
    initial?.protocol || preset?.protocol || 'openai',
  )
  const [baseUrl, setBaseUrl] = useState(
    initial?.baseUrl || preset?.defaultBaseUrl || '',
  )
  const [apiKey, setApiKey] = useState(initial?.apiKey || '')
  const [chatModel, setChatModel] = useState(
    initial?.chatModel || preset?.defaultChatModel || '',
  )
  const [visionModel, setVisionModel] = useState(
    initial?.visionModel || preset?.defaultVisionModel || '',
  )
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [saving, setSaving] = useState(false)

  // 切换 preset 时自动填充默认值
  useEffect(() => {
    if (!preset) return
    if (!initial) {
      setName(preset.name)
      setProtocol(preset.protocol)
      setBaseUrl(preset.defaultBaseUrl)
      setChatModel(preset.defaultChatModel)
      setVisionModel(preset.defaultVisionModel)
    }
  }, [presetId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubmit = async () => {
    setSaving(true)
    try {
      const payload = {
        presetId,
        name: name.trim(),
        protocol,
        baseUrl: baseUrl.trim(),
        apiKey: apiKey.trim(),
        chatModel: chatModel.trim(),
        visionModel: visionModel.trim(),
        enabled,
      }
      await onSubmit(payload)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="provider-form-mask" role="dialog" aria-modal="true">
      <div className="provider-form">
        <header className="provider-form-header">
          <h3>{initial ? '编辑 Provider' : '新增 Provider'}</h3>
          <button
            type="button"
            className="tool-btn"
            onClick={onCancel}
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        <div className="provider-form-row">
          <label>
            <span>Preset</span>
            <select
              value={presetId}
              onChange={(e) => setPresetId(e.target.value)}
              disabled={Boolean(initial)}
            >
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} {p.supportsVision ? '(含视觉)' : '(仅文本)'}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>显示名</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
        </div>

        <div className="provider-form-row">
          <label>
            <span>协议</span>
            <select
              value={protocol}
              onChange={(e) => setProtocol(e.target.value as LLMProtocol)}
            >
              {PROTOCOL_OPTIONS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Base URL</span>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.example.com/v1"
            />
          </label>
        </div>

        <div className="provider-form-row">
          <label>
            <span>API Key</span>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
            />
          </label>
          <label>
            <span>Chat 模型</span>
            <input
              type="text"
              value={chatModel}
              onChange={(e) => setChatModel(e.target.value)}
            />
          </label>
        </div>

        <div className="provider-form-row">
          <label>
            <span>Vision 模型</span>
            <input
              type="text"
              value={visionModel}
              onChange={(e) => setVisionModel(e.target.value)}
            />
          </label>
          <label className="provider-form-toggle">
            <span>启用</span>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
            </label>
        </div>

        <footer className="provider-form-footer">
          <button
            type="button"
            className="tool-btn"
            onClick={onCancel}
            disabled={saving}
          >
            取消
          </button>
          <button
            type="button"
            className="tool-btn primary"
            onClick={handleSubmit}
            disabled={saving || !name.trim() || !apiKey.trim()}
          >
            {saving ? '保存中…' : initial ? '保存修改' : '添加'}
          </button>
        </footer>
      </div>
    </div>
  )
}