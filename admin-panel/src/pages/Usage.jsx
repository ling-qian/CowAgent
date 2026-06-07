import { useState, useEffect } from 'react';
import { Table, Typography, DatePicker, Button, Space, Tag, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { getUsage } from '../api/client';

const { Title } = Typography;
const { RangePicker } = DatePicker;

// 用量统计页面：展示API调用记录和用量详情
export default function Usage() {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 });
  const [dateRange, setDateRange] = useState(null);

  useEffect(() => {
    fetchUsage();
  }, [pagination.current]);

  // 获取用量记录，支持分页和日期范围筛选
  const fetchUsage = async (page = pagination.current) => {
    setLoading(true);
    try {
      const params = { page, per_page: pagination.pageSize };
      // 如果选择了日期范围，添加筛选参数
      if (dateRange && dateRange[0] && dateRange[1]) {
        params.start_date = dateRange[0].format('YYYY-MM-DD');
        params.end_date = dateRange[1].format('YYYY-MM-DD');
      }
      const res = await getUsage(params);
      const data = res.data;
      // 兼容不同的返回格式
      const list = Array.isArray(data) ? data : data?.records || data?.items || [];
      const total = data?.total || list.length;
      setRecords(list);
      setPagination((prev) => ({ ...prev, total }));
    } catch (err) {
      message.error(err.response?.data?.error || '加载用量记录失败');
    } finally {
      setLoading(false);
    }
  };

  // 处理分页变化
  const handleTableChange = (pag) => {
    setPagination((prev) => ({ ...prev, current: pag.current }));
    fetchUsage(pag.current);
  };

  // 处理日期范围筛选
  const handleDateChange = (dates) => {
    setDateRange(dates);
  };

  // 执行筛选查询
  const handleSearch = () => {
    setPagination((prev) => ({ ...prev, current: 1 }));
    fetchUsage(1);
  };

  // 表格列定义
  const columns = [
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 180,
      render: (val) => (val ? new Date(val).toLocaleString('zh-CN') : '-'),
    },
    {
      title: 'API 密钥',
      dataIndex: 'key_prefix',
      key: 'key_prefix',
      width: 120,
      render: (prefix) => <Tag>{prefix || '-'}</Tag>,
    },
    {
      title: '端点',
      dataIndex: 'endpoint',
      key: 'endpoint',
      ellipsis: true,
      render: (val) => val || '-',
    },
    {
      title: '方法',
      dataIndex: 'method',
      key: 'method',
      width: 90,
      render: (val) => {
        if (!val) return '-';
        const colorMap = { GET: 'blue', POST: 'green', PUT: 'orange', DELETE: 'red', PATCH: 'purple' };
        return <Tag color={colorMap[val.toUpperCase()] || 'default'}>{val}</Tag>;
      },
    },
    {
      title: '状态码',
      dataIndex: 'status_code',
      key: 'status_code',
      width: 100,
      render: (code) => {
        if (!code) return '-';
        const color = code < 300 ? 'green' : code < 400 ? 'blue' : code < 500 ? 'orange' : 'red';
        return <Tag color={color}>{code}</Tag>;
      },
    },
    {
      title: '请求ID',
      dataIndex: 'request_id',
      key: 'request_id',
      ellipsis: true,
      render: (val) => val || '-',
    },
    {
      title: '耗时(ms)',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 100,
      render: (val) => (val !== undefined && val !== null ? val.toFixed(1) : '-'),
    },
  ];

  return (
    <div>
      <Title level={4} style={{ marginBottom: 24 }}>用量统计</Title>

      {/* 筛选条件 */}
      <Space style={{ marginBottom: 16 }}>
        <RangePicker onChange={handleDateChange} placeholder={['开始日期', '结束日期']} />
        <Button type="primary" icon={<ReloadOutlined />} onClick={handleSearch}>
          查询
        </Button>
      </Space>

      <Table
        columns={columns}
        dataSource={records}
        rowKey={(record) => record.id || record.request_id || Math.random().toString()}
        loading={loading}
        pagination={{
          ...pagination,
          showTotal: (total) => `共 ${total} 条记录`,
          showSizeChanger: false,
        }}
        onChange={handleTableChange}
      />
    </div>
  );
}
