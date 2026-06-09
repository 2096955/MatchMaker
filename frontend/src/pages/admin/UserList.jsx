import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getUsers, deleteUser } from '../../api'
import ConfirmModal from '../../components/ConfirmModal'

export default function UserList() {
  const [users, setUsers]       = useState([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const [toDelete, setToDelete] = useState(null)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { const { data } = await getUsers(); setUsers(data) }
    catch { setError('Failed to load users') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const handleDelete = async () => {
    try { await deleteUser(toDelete.user_id); setToDelete(null); load() }
    catch (err) { setError(err.response?.data?.error || 'Delete failed'); setToDelete(null) }
  }

  return (
    <>
      <div className="page-header" style={{ maxWidth: 946 }}>
        <div>
          <div className="page-title">Users</div>
          <div className="page-sub">Manage user accounts and role assignments</div>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/admin/users/new')}>+ Add User</button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="card" style={{ maxWidth: 946 }}>
        <div className="card-header"><span>{users.length} user{users.length !== 1 ? 's' : ''}</span></div>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div>
        ) : users.length === 0 ? (
          <div className="empty-state"><div className="empty-icon">👤</div><p>No users found.</p></div>
        ) : (
          <table>
            <thead>
              <tr><th>Username</th><th>Full Name</th><th>Email</th><th>Roles</th><th>Status</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.user_id}>
                  <td><strong>{u.username}</strong></td>
                  <td>{u.full_name || '—'}</td>
                  <td style={{ color: '#6b7280' }}>{u.email}</td>
                  <td>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {u.roles?.length > 0
                        ? u.roles.map(r => <span key={r.role_id} className="badge badge-indigo">{r.role_name}</span>)
                        : <span style={{ color: '#9ca3af', fontSize: 12 }}>None</span>}
                    </div>
                  </td>
                  <td>
                    <span className={`badge ${u.is_active ? 'badge-green' : 'badge-red'}`}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td>
                    <div className="action-cell">
                      <button className="btn btn-ghost btn-sm"
                        onClick={() => navigate(`/admin/users/${u.user_id}/edit`)}>Edit</button>
                      <button className="btn btn-danger btn-sm"
                        onClick={() => setToDelete(u)}>Delete</button>
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
          title="Delete User"
          message={`Delete user "${toDelete.username}"? This cannot be undone.`}
          onConfirm={handleDelete}
          onCancel={() => setToDelete(null)}
        />
      )}
    </>
  )
}
