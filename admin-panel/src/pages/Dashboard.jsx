import { useState, useEffect } from 'react';
import { Card, Col, Row, Statistic, Spin, Alert, Typography } from 'antd';
import {
  TeamOutlined,
  KeyOutlined,
  ApiOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import { getTenantStats } from '../api/client';

const { Title } = Typography;

// 仪表盘页面：展示租户的统计概览信息
export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // 加载租户统计数据
  useEffect(() => {
    fetchStats();
  }, []);

  const fetchStats = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getTenantStats();
      setStats(res.data);
    } catch (err) {
      setError(err.response?.data?.error || '加载统计数据失败');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  if (error) {
    return <Alert type="error" message={error} showIcon />;
  }

  return (
    <div>
      <Title level={4} style={{ marginBottom: 24 }}>
        仪表盘
      </Title>

      <Row gutter={[16, 16]}>
        {/* 租户名称 */}
        <Col xs={24} sm={12} lg={6}>
          <Card bordered={false}>
            <Statistic
              title="租户名称"
              value={stats?.tenant_name || stats?.name || '-'}
              prefix={<TeamOutlined />}
            />
          </Card>
        </Col>

        {/* API密钥数量 */}
        <Col xs={24} sm={12} lg={6}>
          <Card bordered={false}>
            <Statistic
              title="API 密钥数"
              value={stats?.key_count ?? 0}
              prefix={<KeyOutlined />}
            />
          </Card>
        </Col>

        {/* 本月API调用次数 */}
        <Col xs={24} sm={12} lg={6}>
          <Card bordered={false}>
            <Statistic
              title="本月调用次数"
              value={stats?.monthly_calls ?? 0}
              prefix={<ApiOutlined />}
            />
          </Card>
        </Col>

        {/* 配额限制 */}
        <Col xs={24} sm={12} lg={6}>
          <Card bordered={false}>
            <Statistic
              title="月度配额"
              value={stats?.monthly_limit ?? '无限制'}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* 租户详细信息 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} sm={12}>
          <Card bordered={false} title="租户信息">
            <p><strong>租户ID：</strong>{stats?.tenant_id || '-'}</p>
            <p><strong>套餐：</strong>{stats?.plan || '-'}</p>
            <p><strong>状态：</strong>{stats?.active !== undefined ? (stats.active ? '活跃' : '停用') : '-'}</p>
          </Card>
        </Col>
        <Col xs={24} sm={12}>
          <Card bordered={false} title="用量概览">
            <p><strong>今日调用：</strong>{stats?.today_calls ?? 0}</p>
            <p><strong>本周调用：</strong>{stats?.weekly_calls ?? 0}</p>
            <p><strong>配额使用率：</strong>
              {stats?.monthly_limit
                ? `${((stats?.monthly_calls ?? 0) / stats.monthly_limit * 100).toFixed(1)}%`
                : '无限制'}
            </p>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
