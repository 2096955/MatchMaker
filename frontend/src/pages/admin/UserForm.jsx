import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getUser, addUser, editUser, getRoles } from '../../api'

export default function UserForm() {
  const { id } = useParams()
  const isEdit = Boolean(id)
  const navigate = useNavigate()

  const [form, setForm]         = useState({ username: '', email: '', full_name: '', password: '', is_active: true })
  const [roleIds, setRoleIds]   = useState([])
  const [allRoles, setAllRoles] = useState([])
  const [errors, setErrors]     = useState({})
  const [apiError, setApiError] = useState('')
  const [loading, setLoading]   = useState(false)
  const [saving, setSaving]     = useState(false)
  const [showPwd, setShowPwd]   = useState(false)

  useEffect(() => {
    getRoles().then(({ data }) => setAllRoles(data)).catch(() => {})
    if (!isEdit) return
    setLoading(true)
    getUser(id)
      .then(({ data }) => {
        setForm({ username: data.username, email: data.email, full_name: data.full_name || '', password: '', is_active: Boolean(data.is_active) })
        setRoleIds((data.roles || []).map(r => r.role_id))
      })
      .catch(() => setApiError('Failed to load user'))
      .finally(() => setLoading(false))
  }, [id, isEdit])

  const set = (field, val) => {
    setForm(f => ({ ...f, [field]: val }))
    setErrors(e => ({ ...e, [field]: '' }))
  }

  const toggleRole = rid => {
    setRoleIds(ids => ids.includes(rid) ? ids.filter(i => i !== rid) : [...ids, rid])
  }

  const validate = () => {
    const e = {}
    if (!form.username.trim()) e.username = 'Username is required'
    if (!form.email.trim())    e.email    = 'Email is required'
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) e.email = 'Invalid email address'
    if (!isEdit || form.password) {
      if (!form.password) e.password = 'Password is required'
      else if (!/(?=.*[A-Z])(?=.*\d).{8,}/.test(form.password))
        e.password = 'Min 8 chars, one uppercase, one digit'
    }
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async ev => {
    ev.preventDefault()
    if (!validate()) return
    setSaving(true); setApiError('')
    try {
      const payload = { ...form, role_ids: roleIds }
      if (isEdit) await editUser(id, payload)
      else        await addUser(payload)
      navigate('/admin/users')
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
          <div className="page-title">{isEdit ? 'Edit User' : 'Add User'}</div>
          <div className="page-sub">Manage user account and role assignments</div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button type="button" className="btn btn-ghost" onClick={() => navigate('/admin/users')}>Cancel</button>
          <button type="submit" form="user-form" className="btn btn-primary" disabled={saving} aria-busy={saving}>
            {saving ? <><span className="spinner" style={{ width: 14, height: 14 }} aria-hidden="true" /> Saving…</> : isEdit ? 'Save Changes' : 'Create User'}
          </button>
        </div>
      </div>

      {apiError && <div className="alert alert-error" role="alert">{apiError}</div>}

      <form id="user-form" onSubmit={handleSubmit} className="form-wrap" noValidate aria-label={isEdit ? 'Edit user form' : 'Add user form'}>
        <div className="form-section">
          <div className="form-section-title">Account Details</div>
          <div className="form-grid">
            <div className="form-group">
              <label className="form-label" htmlFor="f-username">
                Username <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <input id="f-username" className="f-input"
                required aria-required="true"
                aria-invalid={errors.username ? 'true' : undefined}
                aria-describedby={errors.username ? 'e-username' : undefined}
                value={form.username}
                onChange={e => set('username', e.target.value)} placeholder="jdoe" />
              {errors.username && <span id="e-username" className="form-error" role="alert">{errors.username}</span>}
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="f-full-name">Full Name</label>
              <input id="f-full-name" className="f-input" value={form.full_name}
                onChange={e => set('full_name', e.target.value)} placeholder="John Doe" />
            </div>
            <div className="form-group form-col-2">
              <label className="form-label" htmlFor="f-email">
                Email <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <input id="f-email" className="f-input" type="email"
                required aria-required="true"
                aria-invalid={errors.email ? 'true' : undefined}
                aria-describedby={errors.email ? 'e-email' : undefined}
                value={form.email}
                onChange={e => set('email', e.target.value)} placeholder="jdoe@company.com" />
              {errors.email && <span id="e-email" className="form-error" role="alert">{errors.email}</span>}
            </div>
            <div className="form-group form-col-2">
              <label className="form-label" htmlFor="f-password">
                Password{' '}
                {isEdit
                  ? <span style={{ color: '#6b7280', fontWeight: 400 }}>(leave blank to keep current)</span>
                  : <><span aria-hidden="true">*</span><span className="sr-only"> (required)</span></>}
              </label>
              <div style={{ position: 'relative' }}>
                <input id="f-password" className="f-input"
                  type={showPwd ? 'text' : 'password'}
                  aria-required={!isEdit}
                  aria-invalid={errors.password ? 'true' : undefined}
                  aria-describedby={errors.password ? 'e-password' : undefined}
                  value={form.password}
                  onChange={e => set('password', e.target.value)}
                  placeholder="Min 8 chars, one uppercase, one digit"
                  style={{ width: '100%', paddingRight: 44 }} />
                <button type="button"
                  aria-label={showPwd ? 'Hide password' : 'Show password'}
                  style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 16 }}
                  onClick={() => setShowPwd(v => !v)}>
                  {showPwd ? '🙈' : '👁️'}
                </button>
              </div>
              {errors.password && <span id="e-password" className="form-error" role="alert">{errors.password}</span>}
            </div>
            <div className="form-group form-col-2">
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                <input type="checkbox" checked={form.is_active}
                  onChange={e => set('is_active', e.target.checked)}
                  style={{ width: 16, height: 16, accentColor: '#6366f1' }} />
                Active account
              </label>
            </div>
          </div>
        </div>

        <div className="form-section">
          <div className="form-section-title">Role Assignments</div>
          <div style={{ padding: '14px 20px', display: 'flex', flexWrap: 'wrap', gap: 10 }}>
            {allRoles.length === 0 ? (
              <span style={{ color: '#9ca3af', fontSize: 13 }}>No roles available — create roles first</span>
            ) : allRoles.map(r => (
              <label key={r.role_id} style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer',
                padding: '7px 14px', border: `2px solid ${roleIds.includes(r.role_id) ? '#6366f1' : '#e5e7eb'}`,
                borderRadius: 8, fontSize: 13, fontWeight: 500,
                background: roleIds.includes(r.role_id) ? '#eef2ff' : '#fff',
                color: roleIds.includes(r.role_id) ? '#4f46e5' : '#374151',
                transition: 'all .15s' }}>
                <input type="checkbox" checked={roleIds.includes(r.role_id)}
                  onChange={() => toggleRole(r.role_id)}
                  style={{ accentColor: '#6366f1' }} />
                {r.role_name}
              </label>
            ))}
          </div>
        </div>

      </form>
    </>
  )
}
