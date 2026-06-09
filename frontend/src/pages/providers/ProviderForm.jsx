import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getProvider, addProvider, editProvider } from '../../api'

const EMPTY = { provider_name: '', provider_desc: '', provider_website: '' }

export default function ProviderForm() {
  const { id } = useParams()
  const isEdit  = Boolean(id)
  const navigate = useNavigate()

  const [form, setForm]       = useState(EMPTY)
  const [errors, setErrors]   = useState({})
  const [apiError, setApiError] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving]   = useState(false)

  useEffect(() => {
    if (!isEdit) return
    setLoading(true)
    getProvider(id)
      .then(({ data }) => setForm({
        provider_name: data.provider_name || '',
        provider_desc: data.provider_desc || '',
        provider_website: data.provider_website || '',
      }))
      .catch(() => setApiError('Failed to load provider'))
      .finally(() => setLoading(false))
  }, [id, isEdit])

  const set = (field, val) => {
    setForm(f => ({ ...f, [field]: val }))
    setErrors(e => ({ ...e, [field]: '' }))
  }

  const validate = () => {
    const e = {}
    if (!form.provider_name.trim()) e.provider_name = 'Provider name is required'
    if (form.provider_website && !/^https?:\/\/.+/.test(form.provider_website))
      e.provider_website = 'Must start with http:// or https://'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async e => {
    e.preventDefault()
    if (!validate()) return
    setSaving(true); setApiError('')
    try {
      if (isEdit) await editProvider(id, form)
      else        await addProvider(form)
      navigate('/providers')
    } catch (err) {
      setApiError(err.response?.data?.error || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div>

  return (
    <>
      <div className="page-header" style={{ maxWidth: 946 }}>
        <div>
          <div className="page-title">{isEdit ? 'Edit Provider' : 'Add Provider'}</div>
          <div className="page-sub">
            {isEdit ? 'A new version will be created on save' : 'Register a new third-party data provider'}
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button type="button" className="btn btn-ghost" onClick={() => navigate('/providers')}>Cancel</button>
          <button type="submit" form="provider-form" className="btn btn-primary" disabled={saving} aria-busy={saving}>
            {saving ? <><span className="spinner" style={{ width: 14, height: 14 }} aria-hidden="true" /> Saving…</> : isEdit ? 'Save Changes' : 'Add Provider'}
          </button>
        </div>
      </div>

      {apiError && <div className="alert alert-error" role="alert">{apiError}</div>}

      <form id="provider-form" onSubmit={handleSubmit} className="form-wrap" noValidate aria-label={isEdit ? 'Edit provider form' : 'Add provider form'}>
        <div className="form-section">
          <div className="form-section-title">Provider Details</div>
          <div className="form-grid">
            <div className="form-group form-col-2">
              <label className="form-label" htmlFor="f-provider-name">
                Provider Name <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <input id="f-provider-name" className="f-input"
                required aria-required="true"
                aria-invalid={errors.provider_name ? 'true' : undefined}
                aria-describedby={errors.provider_name ? 'e-provider-name' : undefined}
                value={form.provider_name}
                onChange={e => set('provider_name', e.target.value)}
                placeholder="e.g. Bloomberg, Reuters, S&P" />
              {errors.provider_name && <span id="e-provider-name" className="form-error" role="alert">{errors.provider_name}</span>}
            </div>

            <div className="form-group form-col-2">
              <label className="form-label" htmlFor="f-provider-desc">Description</label>
              <textarea id="f-provider-desc" className="f-textarea" rows={3} value={form.provider_desc}
                onChange={e => set('provider_desc', e.target.value)}
                placeholder="Brief description of this data provider" />
            </div>

            <div className="form-group form-col-2">
              <label className="form-label" htmlFor="f-provider-website">Website</label>
              <input id="f-provider-website" className="f-input"
                type="url"
                aria-invalid={errors.provider_website ? 'true' : undefined}
                aria-describedby={errors.provider_website ? 'e-provider-website' : undefined}
                value={form.provider_website}
                onChange={e => set('provider_website', e.target.value)}
                placeholder="https://provider.com" />
              {errors.provider_website && <span id="e-provider-website" className="form-error" role="alert">{errors.provider_website}</span>}
            </div>
          </div>
        </div>

      </form>
    </>
  )
}
