import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import ProviderList from './pages/providers/ProviderList'
import ProviderForm from './pages/providers/ProviderForm'
import DatasetList       from './pages/datasets/DatasetList'
import DatasetForm       from './pages/datasets/DatasetForm'
import DatasetTransforms from './pages/datasets/DatasetTransforms'
import RoleList     from './pages/admin/RoleList'
import RoleForm     from './pages/admin/RoleForm'
import UserList     from './pages/admin/UserList'
import UserForm     from './pages/admin/UserForm'
import ReportList        from './pages/reports/ReportList'
import ReportDetail     from './pages/reports/ReportDetail'
import IngestionConsole from './pages/ingestion/IngestionConsole'
import CatalogueList    from './pages/catalogue/CatalogueList'
import CatalogueDetail  from './pages/catalogue/CatalogueDetail'
import MappingDemo      from './pages/mapping/MappingDemo'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/providers" replace />} />
        <Route path="/providers"          element={<ProviderList />} />
        <Route path="/providers/new"      element={<ProviderForm />} />
        <Route path="/providers/:id/edit" element={<ProviderForm />} />
        <Route path="/datasets"           element={<DatasetList />} />
        <Route path="/datasets/new"             element={<DatasetForm />} />
        <Route path="/datasets/:id/edit"        element={<DatasetForm />} />
        <Route path="/datasets/:id/transforms"  element={<DatasetTransforms />} />
        <Route path="/admin/roles"        element={<RoleList />} />
        <Route path="/admin/roles/new"    element={<RoleForm />} />
        <Route path="/admin/roles/:id/edit" element={<RoleForm />} />
        <Route path="/admin/users"        element={<UserList />} />
        <Route path="/admin/users/new"    element={<UserForm />} />
        <Route path="/admin/users/:id/edit" element={<UserForm />} />
        <Route path="/ingestion"    element={<IngestionConsole />} />
        <Route path="/reports"      element={<ReportList />} />
        <Route path="/reports/:id"  element={<ReportDetail />} />
        <Route path="/catalogue"                   element={<CatalogueList />} />
        <Route path="/catalogue/:vendor/:ref"      element={<CatalogueDetail />} />
        <Route path="/mapping"                     element={<MappingDemo />} />
      </Routes>
    </Layout>
  )
}
