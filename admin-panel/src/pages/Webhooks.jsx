import { useState, useEffect } from 'react';
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Tag,
  Space,
  Typography,
  message,
  Popconfirm,
  Switch,
} from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { getWebhooks, createWebhook, deleteWebhook } from '../api/client';

const { Title, Text } = Typography;

const EVENT_OPTIONS = [
  { value: 'message.created', label: '消息创建' },
  { value: 'message.replied', label: '消息回复' },
  { value: 'tenant.updated', label: '租户更新' },
  { value: 'api_key.created', label: '密钥创建' },
  { value: 'api_key.revoked', label: '密钥撤销' },
  { value: 'billing.quota_exceeded', label: '配额超限' },
  { value: 'billing.invoice_created', label: '账单生成' },
];

export default function Webhooks() {
  const [webhooks, setWebhooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    fetchWebhooks();
  }, []);

  const fetchWebhooks = async () => {
    setLoading(true);
    try {
      const res = await getWebhooks();
      const list = res.data?.webhooks || [];
      setWebhooks(list);
    } catch (err) {
      message.error(err.response?.data?.error || '加载 Webhook 列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (values) => {
    setCreating(true);
    try {
      await createWebhook(values);
      message.success('Webhook 创建成功');
      form.resetFields();
      setModalOpen(false);
      fetchWebhooks();
    } catch (err) {
      message.error(err.response?.data?.error || '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (webhookId) => {
    try {
      await deleteWebhook(webhookId);
      message.success('Webhook 已删除');
      fetchWebhooks();
    } catch (err) {
      message.error(err.response?.data?.error || '删除失败');
    }
  };

  const columns = [
    {
      title: 'URL',
      dataIndex: 'url',
      key: 'url',
      ellipsis: true,
      render: (url) => <Text code>{url}</Text>,
    },
    {
      title: '事件',
      dataIndex: 'events',
      key: 'events',
      width: 200,
      render: (events) => {
        if (!events) return '-';
        const list = typeof events === 'string' ? events.split(',') : events;
        return (
          <Space size={[4, 4]} wrap>
            {list.map((e, i) => (
              <Tag key={i} color="blue">{e}</Tag>
            ))}
          </Space>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (active) =>
        active !== false ? <Tag color="green">启用</Tag> : <Tag color="red">禁用</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (val) => (val ? new Date(val).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, record) => (
        <Popconfirm
          title="确认删除"
          description="删除后该 Webhook 将不再接收事件通知，确定要继续吗？"
          onConfirm={() => handleDelete(record.id)}
          okText="确定"
          cancelText="取消"
        >
          <Button type="link" danger size="small" icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>Webhook 管理</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          创建 Webhook
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={webhooks}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 10 }}
      />

      <Modal
        title="创建 Webhook"
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
        width={560}
      >
        <Form form={form} onFinish={handleCreate} layout="vertical">
          <Form.Item
            name="url"
            label="回调 URL"
            rules={[
              { required: true, message: '请输入回调 URL' },
              { type: 'url', message: '请输入有效的 URL' },
            ]}
          >
            <Input placeholder="https://example.com/webhook" />
          </Form.Item>
          <Form.Item
            name="events"
            label="订阅事件"
            rules={[{ required: true, message: '请选择至少一个事件' }]}
          >
            <Input placeholder="message.created,message.replied" />
          </Form.Item>
          <Form.Item name="secret" label="签名密钥（可选）">
            <Input.Password placeholder="用于 HMAC-SHA256 签名验证" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
