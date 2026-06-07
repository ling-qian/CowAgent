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
  Alert,
} from 'antd';
import { PlusOutlined, CopyOutlined } from '@ant-design/icons';
import { getApiKeys, createApiKey, revokeApiKey } from '../api/client';

const { Title, Text } = Typography;

// API密钥管理页面：列出、创建和撤销API密钥
export default function ApiKeys() {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newKeyData, setNewKeyData] = useState(null); // 创建成功后显示的密钥数据
  const [form] = Form.useForm();

  useEffect(() => {
    fetchKeys();
  }, []);

  // 获取所有API密钥列表
  const fetchKeys = async () => {
    setLoading(true);
    try {
      const res = await getApiKeys();
      // 兼容返回格式：可能是数组或包含keys字段的对象
      const list = Array.isArray(res.data) ? res.data : res.data?.keys || [];
      setKeys(list);
    } catch (err) {
      message.error(err.response?.data?.error || '加载密钥列表失败');
    } finally {
      setLoading(false);
    }
  };

  // 创建新的API密钥
  const handleCreate = async (values) => {
    setCreating(true);
    try {
      const res = await createApiKey({ name: values.name });
      message.success('密钥创建成功');
      setNewKeyData(res.data); // 保存新创建的密钥信息，用于展示
      form.resetFields();
      setModalOpen(false);
      fetchKeys();
    } catch (err) {
      message.error(err.response?.data?.error || '创建密钥失败');
    } finally {
      setCreating(false);
    }
  };

  // 撤销（删除）API密钥
  const handleRevoke = async (keyId) => {
    try {
      await revokeApiKey(keyId);
      message.success('密钥已撤销');
      fetchKeys();
    } catch (err) {
      message.error(err.response?.data?.error || '撤销密钥失败');
    }
  };

  // 复制密钥到剪贴板
  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text).then(() => {
      message.success('已复制到剪贴板');
    });
  };

  // 表格列定义
  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
    },
    {
      title: '密钥前缀',
      dataIndex: 'key_prefix',
      key: 'key_prefix',
      render: (prefix) => <Text code>{prefix || '-'}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'active',
      key: 'active',
      render: (active) =>
        active !== false ? (
          <Tag color="green">活跃</Tag>
        ) : (
          <Tag color="red">已撤销</Tag>
        ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (val) => (val ? new Date(val).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '最后使用',
      dataIndex: 'last_used_at',
      key: 'last_used_at',
      render: (val) => (val ? new Date(val).toLocaleString('zh-CN') : '从未使用'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_, record) => (
        <Popconfirm
          title="确认撤销"
          description="撤销后该密钥将立即失效，确定要继续吗？"
          onConfirm={() => handleRevoke(record.id)}
          okText="确定"
          cancelText="取消"
        >
          <Button type="link" danger size="small" disabled={record.active === false}>
            撤销
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>API 密钥管理</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          创建密钥
        </Button>
      </div>

      {/* 新创建密钥的提示信息 */}
      {newKeyData && (
        <Alert
          type="success"
          message="密钥创建成功"
          description={
            <div>
              <p>请立即保存以下密钥，关闭后将无法再次查看：</p>
              <Text code copyable={{ onCopy: () => message.success('已复制') }}>
                {newKeyData.key || newKeyData.api_key}
              </Text>
            </div>
          }
          closable
          onClose={() => setNewKeyData(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      <Table
        columns={columns}
        dataSource={keys}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 10 }}
      />

      {/* 创建密钥的弹窗 */}
      <Modal
        title="创建新密钥"
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
            name="name"
            label="密钥名称"
            rules={[{ required: true, message: '请输入密钥名称' }]}
          >
            <Input placeholder="例如：生产环境密钥" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
