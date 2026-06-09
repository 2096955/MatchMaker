import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getRoles, deleteRole } from '../../api'
import ConfirmModal from '../../components/ConfirmModal'

export default function RoleList() {
  const [roles, setRoles]     = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')
  const [toDelete, setToDelete] = useState(null)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { const { data } = await getRoles(); setRoles(data) }
    catch { setError('Failed to load roles') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const handleDelete = async () => {
    try { await deleteRole(toDelete.role_id); setToDelete(null); load() }
    catch (err) { setError(err.response?.data?.error || 'Delete failed'); setToDelete(null) }
  }

  const privSummary = privs => {
    const lines = []
    const byType = {}
    privs.forEach(p => {
      const t = p.resource_type
      if (!byType[t]) byType[t] = []
      const ops = [p.can_add && 'Add', p.can_modify && 'Edit', p.can_delete && 'Delete'].filter(Boolean)
      byType[t].push(ops.join('/'))
    })
    Object.entries(byType).forEach(([t, ops]) => lines.push(`${t}: ${ops.join(', ')}`))
    return lines.join(' | ') || 'No privileges'
  }

  return (
    <>
      <div className="page-header" style={{ maxWidth: 946 }}>
        <div>
          <div className="page-title">Roles</div>
          <div className="page-sub">Define roles and their access privileges</div>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/admin/roles/new')}>+ Add Role</button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="card" style={{ maxWidth: 946 }}>
        <div className="card-header"><span>{roles.length} role{roles.length !== 1 ? 's' : ''}</span></div>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div>
        ) : roles.length === 0 ? (
          <div className="empty-state"><div className="empty-icon">🛡️</div><p>No roles defined.</p></div>
        ) : (
          <table>
            <thead>
              <tr><th>Role Name</th><th>Description</th><th>Privileges</th><th>Status</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {roles.map(r => (
                <tr key={r.role_id}>
                  <td><strong>{r.role_name}</strong></td>
                  <td style={{ color: '#6b7280' }}>{r.role_desc || '—'}</td>
                  <td style={{ color: '#6b7280', fontSize: 12 }}>{privSummary(r.privileges || [])}</td>
                  <td>
                    <span className={`badge ${r.is_active ? 'badge-green' : 'badge-red'}`}>
                      {r.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td>
                    <div className="action-cell">
                      <button className="btn btn-ghost btn-sm"
                        onClick={() => navigate(`/admin/roles/${r.role_id}/edit`)}>Edit</button>
                      <button className="btn btn-danger btn-sm"
                        onClick={() => setToDelete(r)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {toDelete && (
        <ConfirmModal
          title="Delete Role"
          message={`Delete role "${toDelete.role_name}"? Users assigned to this role will lose these privileges.`}
          onConfirm={handleDelete}
          onCancel={() => setToDelete(null)}
        />
      )}
    </>
  )
}
