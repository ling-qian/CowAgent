import { useState, useEffect } from 'react';
import { Table, Typography, Tag, Input, DatePicker, Space, message } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { getAuditLogs } from '../api/client';

const { Title } = Typography;
const { RangePicker } = DatePicker;

const ACTION_COLORS = {
  create: 'green',
  update: 'blue',
  delete: 'red',
  login: 'cyan',
  api_call: 'default',
  register: 'purple',
};

export default function AuditLogs() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 });
  const [actionFilter, setActionFilter] = useState('');

  useEffect(() => {
    fetchLogs(1);
  }, []);

  const fetchLogs = async (page = 1, action = actionFilter) => {
    setLoading(true);
    try {
      const params = { page, per_page: 20 };
      if (action) params.action = action;
      const res = await getAuditLogs(params);
      const data = res.data;
      setLogs(data?.logs || data || []);
      setPagination({
        current: page,
        pageSize: 20,
        total: data?.total || 0,
      });
    } catch (err) {
      message.error(err.response?.data?.error || '加载审计日志失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = (value) => {
    setActionFilter(value);
    fetchLogs(1, value);
  };

  const columns = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (val) => (val ? new Date(val).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      dataIndex: 'action',
      key: 'action',
      width: 120,
      render: (action) => (
        <Tag color={ACTION_COLORS[action] || 'default'}>{action || '-'}</Tag>
      ),
    },
    {
      title: '资源',
      dataIndex: 'resource_type',
      key: 'resource_type',
      width: 120,
      render: (type) => type || '-',
    },
    {
      title: '资源 ID',
      dataIndex: 'resource_id',
      key: 'resource_id',
      width: 160,
      ellipsis: true,
      render: (id) => id || '-',
    },
    {
      title: '详情',
      dataIndex: 'details',
      key: 'details',
      ellipsis: true,
      render: (details) => {
        if (!details) return '-';
        try {
          const obj = typeof details === 'string' ? JSON.parse(details) : details;
          return JSON.stringify(obj, null, 0).slice(0, 100);
        } catch {
          return String(details).slice(0, 100);
        }
      },
    },
    {
      title: 'IP 地址',
      dataIndex: 'ip_address',
      key: 'ip_address',
      width: 140,
      render: (ip) => ip || '-',
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>审计日志</Title>
        <Space>
          <Input.Search
            placeholder="按操作类型筛选"
            allowClear
            onSearch={handleSearch}
            style={{ width: 200 }}
            prefix={<SearchOutlined />}
          />
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={logs}
        rowKey={(r) => r.id || `${r.created_at}-${r.action}`}
        loading={loading}
        pagination={{
          ...pagination,
          showTotal: (total) => `共 ${total} 条`,
          onChange: (page) => fetchLogs(page),
        }}
      />
    </div>
  );
}
