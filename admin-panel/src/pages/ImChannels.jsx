import { useState, useEffect } from 'react';
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Select,
  Tag,
  Space,
  Typography,
  message,
  Popconfirm,
} from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { getImChannels, createImChannel, deleteImChannel } from '../api/client';

const { Title } = Typography;

const CHANNEL_TYPES = [
  { value: 'feishu', label: '飞书' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'wechat_mp', label: '微信公众号' },
  { value: 'wechat_com', label: '企业微信' },
  { value: 'wechat_kf', label: '微信客服' },
  { value: 'wecom_bot', label: '企微机器人' },
];

const CHANNEL_COLORS = {
  feishu: 'blue',
  dingtalk: 'cyan',
  wechat_mp: 'green',
  wechat_com: 'geekblue',
  wechat_kf: 'lime',
  wecom_bot: 'purple',
};

export default function ImChannels() {
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    fetchMappings();
  }, []);

  const fetchMappings = async () => {
    setLoading(true);
    try {
      const res = await getImChannels();
      const list = res.data?.mappings || [];
      setMappings(list);
    } catch (err) {
      message.error(err.response?.data?.error || '加载 IM 渠道列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (values) => {
    setCreating(true);
    try {
      await createImChannel(values);
      message.success('IM 渠道映射创建成功');
      form.resetFields();
      setModalOpen(false);
      fetchMappings();
    } catch (err) {
      message.error(err.response?.data?.error || '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (mappingId) => {
    try {
      await deleteImChannel(mappingId);
      message.success('映射已删除');
      fetchMappings();
    } catch (err) {
      message.error(err.response?.data?.error || '删除失败');
    }
  };

  const columns = [
    {
      title: '渠道类型',
      dataIndex: 'channel_type',
      key: 'channel_type',
      width: 140,
      render: (type) => {
        const item = CHANNEL_TYPES.find((c) => c.value === type);
        return <Tag color={CHANNEL_COLORS[type] || 'default'}>{item?.label || type}</Tag>;
      },
    },
    {
      title: 'App ID',
      dataIndex: 'app_id',
      key: 'app_id',
      ellipsis: true,
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
          description="删除后该 IM 渠道将无法自动识别租户，确定要继续吗？"
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
        <Title level={4} style={{ margin: 0 }}>IM 渠道管理</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          添加渠道
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={mappings}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 10 }}
      />

      <Modal
        title="添加 IM 渠道映射"
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} onFinish={handleCreate} layout="vertical">
          <Form.Item
            name="channel_type"
            label="渠道类型"
            rules={[{ required: true, message: '请选择渠道类型' }]}
          >
            <Select options={CHANNEL_TYPES} placeholder="请选择 IM 渠道" />
          </Form.Item>
          <Form.Item
            name="app_id"
            label="App ID"
            rules={[{ required: true, message: '请输入 App ID' }]}
          >
            <Input placeholder="例如：cli_xxxxx（飞书）/ dingxxxxx（钉钉）" />
          </Form.Item>
          <Form.Item name="app_secret" label="App Secret">
            <Input.Password placeholder="应用密钥（可选，加密存储）" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
