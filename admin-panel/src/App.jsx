import { Routes, Route, Navigate } from 'react-router-dom';
import { Layout, Menu, Button, Typography } from 'antd';
import {
  DashboardOutlined,
  KeyOutlined,
  BarChartOutlined,
  SettingOutlined,
  LogoutOutlined,
  MessageOutlined,
  LinkOutlined,
  FileSearchOutlined,
  CommentOutlined,
} from '@ant-design/icons';
import { useState, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import ApiKeys from './pages/ApiKeys';
import Usage from './pages/Usage';
import Settings from './pages/Settings';
import ImChannels from './pages/ImChannels';
import Webhooks from './pages/Webhooks';
import AuditLogs from './pages/AuditLogs';
import Chat from './pages/Chat';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

// 菜单项配置
const menuItems = [
  { key: '/chat', icon: <CommentOutlined />, label: 'AI 对话' },
  { key: '/dashboard', icon: <DashboardOutlined />, label: '仪表盘' },
  { key: '/api-keys', icon: <KeyOutlined />, label: 'API 密钥' },
  { key: '/usage', icon: <BarChartOutlined />, label: '用量统计' },
  { key: '/im-channels', icon: <MessageOutlined />, label: 'IM 渠道' },
  { key: '/webhooks', icon: <LinkOutlined />, label: 'Webhook' },
  { key: '/audit-logs', icon: <FileSearchOutlined />, label: '审计日志' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
];

// 主布局组件，包含侧边栏和顶部导航
function MainLayout({ onLogout }) {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  // 退出登录：清除本地存储的API Key并跳转到登录页
  const handleLogout = () => {
    onLogout();
    navigate('/login');
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed}>
        <div
          style={{
            height: 32,
            margin: 16,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Title level={4} style={{ color: '#fff', margin: 0, whiteSpace: 'nowrap' }}>
            {collapsed ? 'CA' : 'CowAgent'}
          </Title>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            padding: '0 24px',
            background: '#fff',
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
          }}
        >
          <Button
            type="text"
            icon={<LogoutOutlined />}
            onClick={handleLogout}
          >
            退出登录
          </Button>
        </Header>
        <Content style={{ margin: 24 }}>
          <Routes>
            <Route path="/chat" element={<Chat />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/api-keys" element={<ApiKeys />} />
            <Route path="/usage" element={<Usage />} />
            <Route path="/im-channels" element={<ImChannels />} />
            <Route path="/webhooks" element={<Webhooks />} />
            <Route path="/audit-logs" element={<AuditLogs />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

// 根组件：根据是否已登录决定显示登录页还是主布局
export default function App() {
  const [apiKey, setApiKey] = useState(localStorage.getItem('api_key'));

  const handleLogin = useCallback((key) => {
    localStorage.setItem('api_key', key);
    setApiKey(key);
  }, []);

  const handleLogout = useCallback(() => {
    localStorage.removeItem('api_key');
    setApiKey(null);
  }, []);

  return (
    <Routes>
      <Route path="/login" element={<Login onLogin={handleLogin} />} />
      {/* 已登录时访问根路径跳转到仪表盘 */}
      <Route
        path="/"
        element={
          apiKey ? (
            <Navigate to="/chat" replace />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
      {/* 需要认证的页面，未登录时跳转到登录页 */}
      <Route
        path="/*"
        element={
          apiKey ? (
            <MainLayout onLogout={handleLogout} />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
    </Routes>
  );
}
