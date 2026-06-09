import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getRole, addRole, editRole } from '../../api'

const RESOURCE_TYPES = ['provider', 'dataset']

const defaultPrivs = () => RESOURCE_TYPES.map(rt => ({
  resource_type: rt, resource_id: null,
  can_add: false, can_modify: false, can_delete: false,
}))

export default function RoleForm() {
  const { id } = useParams()
  const isEdit = Boolean(id)
  const navigate = useNavigate()

  const [form, setForm]         = useState({ role_name: '', role_desc: '', is_active: true })
  const [privs, setPrivs]       = useState(defaultPrivs())
  const [errors, setErrors]     = useState({})
  const [apiError, setApiError] = useState('')
  const [loading, setLoading]   = useState(false)
  const [saving, setSaving]     = useState(false)

  useEffect(() => {
    if (!isEdit) return
    setLoading(true)
    getRole(id)
      .then(({ data }) => {
        setForm({ role_name: data.role_name, role_desc: data.role_desc || '', is_active: Boolean(data.is_active) })
        // Map saved privileges onto the default structure
        const saved = {}
        ;(data.privileges || []).forEach(p => { saved[p.resource_type] = p })
        setPrivs(RESOURCE_TYPES.map(rt => saved[rt]
          ? { resource_type: rt, resource_id: null, can_add: Boolean(saved[rt].can_add),
              can_modify: Boolean(saved[rt].can_modify), can_delete: Boolean(saved[rt].can_delete) }
          : { resource_type: rt, resource_id: null, can_add: false, can_modify: false, can_delete: false }
        ))
      })
      .catch(() => setApiError('Failed to load role'))
      .finally(() => setLoading(false))
  }, [id, isEdit])

  const setPriv = (rt, field, val) => {
    setPrivs(ps => ps.map(p => p.resource_type === rt ? { ...p, [field]: val } : p))
  }

  const validate = () => {
    const e = {}
    if (!form.role_name.trim()) e.role_name = 'Role name is required'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async ev => {
    ev.preventDefault()
    if (!validate()) return
    setSaving(true); setApiError('')
    try {
      const payload = { ...form, privileges: privs }
      if (isEdit) await editRole(id, payload)
      else        await addRole(payload)
      navigate('/admin/roles')
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
          <div className="page-title">{isEdit ? 'Edit Role' : 'Add Role'}</div>
          <div className="page-sub">Configure role name and access privileges</div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button type="button" className="btn btn-ghost" onClick={() => navigate('/admin/roles')}>Cancel</button>
          <button type="submit" form="role-form" className="btn btn-primary" disabled={saving} aria-busy={saving}>
            {saving ? <><span className="spinner" style={{ width: 14, height: 14 }} aria-hidden="true" /> Saving…</> : isEdit ? 'Save Changes' : 'Create Role'}
          </button>
        </div>
      </div>

      {apiError && <div className="alert alert-error" role="alert">{apiError}</div>}

      <form id="role-form" onSubmit={handleSubmit} className="form-wrap" noValidate aria-label={isEdit ? 'Edit role form' : 'Add role form'}>
        <div className="form-section">
          <div className="form-section-title">Role Details</div>
          <div className="form-grid">
            <div className="form-group">
              <label className="form-label" htmlFor="f-role-name">
                Role Name <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <input id="f-role-name" className="f-input"
                required aria-required="true"
                aria-invalid={errors.role_name ? 'true' : undefined}
                aria-describedby={errors.role_name ? 'e-role-name' : undefined}
                value={form.role_name}
                onChange={e => { setForm(f => ({ ...f, role_name: e.target.value })); setErrors(e => ({ ...e, role_name: '' })) }}
                placeholder="e.g. analyst" />
              {errors.role_name && <span id="e-role-name" className="form-error" role="alert">{errors.role_name}</span>}
            </div>
            <div className="form-group" style={{ justifyContent: 'flex-end', paddingTop: 20 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                <input type="checkbox" checked={form.is_active}
                  onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))}
                  style={{ width: 16, height: 16, accentColor: '#6366f1' }} />
                Active role
              </label>
            </div>
            <div className="form-group form-col-2">
              <label className="form-label" htmlFor="f-role-desc">Description</label>
              <textarea id="f-role-desc" className="f-textarea" rows={3} value={form.role_desc}
                onChange={e => setForm(f => ({ ...f, role_desc: e.target.value }))}
                placeholder="Describe what this role can do" />
            </div>
          </div>
        </div>

        <div className="form-section">
          <div className="form-section-title">Access Privileges</div>
          <div style={{ padding: '0 20px 16px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 64px 64px 64px', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: '#9ca3af', textTransform: 'uppercase' }}>Resource</span>
              {['Add', 'Mod', 'Delete'].map(op => (
                <span key={op} style={{ fontSize: 11, fontWeight: 700, color: '#9ca3af', textTransform: 'uppercase', textAlign: 'center' }}>{op}</span>
              ))}
            </div>
            {privs.map(p => (
              <div key={p.resource_type} style={{ display: 'grid', gridTemplateColumns: '1fr 64px 64px 64px', alignItems: 'center', padding: '10px 0', borderTop: '1px solid #f3f4f6' }}>
                <span style={{ fontSize: 13, fontWeight: 500, textTransform: 'capitalize' }}>{p.resource_type}</span>
                {['can_add', 'can_modify', 'can_delete'].map(field => (
                  <div key={field} style={{ display: 'flex', justifyContent: 'center' }}>
                    <input type="checkbox"
                      aria-label={`${p.resource_type} — can ${field.replace('can_', '')}`}
                      checked={p[field]}
                      onChange={e => setPriv(p.resource_type, field, e.target.checked)}
                      style={{ width: 16, height: 16, accentColor: '#6366f1', cursor: 'pointer' }} />
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

      </form>
    </>
  )
}
