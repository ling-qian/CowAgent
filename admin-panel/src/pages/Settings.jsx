import { useState, useEffect } from 'react';
import { Form, Input, Button, Card, Typography, message, Spin, Descriptions, Divider } from 'antd';
import { getTenantInfo, updateTenant } from '../api/client';

const { Title } = Typography;

// 设置页面：查看和修改租户配置信息
export default function Settings() {
  const [form] = Form.useForm();
  const [tenant, setTenant] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchTenantInfo();
  }, []);

  // 获取当前租户信息
  const fetchTenantInfo = async () => {
    setLoading(true);
    try {
      const res = await getTenantInfo();
      const data = res.data;
      setTenant(data);
      // 将租户信息填充到表单中
      form.setFieldsValue({
        name: data.name || '',
        webhook_url: data.webhook_url || data.config?.webhook_url || '',
        notification_email: data.notification_email || data.config?.notification_email || '',
        description: data.description || '',
      });
    } catch (err) {
      message.error(err.response?.data?.error || '加载租户信息失败');
    } finally {
      setLoading(false);
    }
  };

  // 提交表单，更新租户配置
  const handleSave = async (values) => {
    setSaving(true);
    try {
      await updateTenant(values);
      message.success('设置已保存');
      fetchTenantInfo();
    } catch (err) {
      message.error(err.response?.data?.error || '保存设置失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  return (
    <div>
      <Title level={4} style={{ marginBottom: 24 }}>设置</Title>

      {/* 租户基本信息展示 */}
      <Card bordered={false} style={{ marginBottom: 24 }}>
        <Descriptions title="租户信息" column={2}>
          <Descriptions.Item label="租户ID">{tenant?.id || tenant?.tenant_id || '-'}</Descriptions.Item>
          <Descriptions.Item label="套餐">{tenant?.plan || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态">
            {tenant?.active !== undefined ? (tenant.active ? '活跃' : '停用') : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {tenant?.created_at ? new Date(tenant.created_at).toLocaleString('zh-CN') : '-'}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Divider />

      {/* 可编辑的租户配置表单 */}
      <Card bordered={false} title="配置编辑">
        <Form
          form={form}
          onFinish={handleSave}
          layout="vertical"
          style={{ maxWidth: 600 }}
        >
          <Form.Item
            name="name"
            label="租户名称"
            rules={[{ required: true, message: '请输入租户名称' }]}
          >
            <Input placeholder="请输入租户名称" />
          </Form.Item>

          <Form.Item
            name="description"
            label="描述"
          >
            <Input.TextArea rows={3} placeholder="请输入租户描述（可选）" />
          </Form.Item>

          <Form.Item
            name="webhook_url"
            label="Webhook URL"
          >
            <Input placeholder="请输入 Webhook 回调地址（可选）" />
          </Form.Item>

          <Form.Item
            name="notification_email"
            label="通知邮箱"
          >
            <Input placeholder="请输入通知接收邮箱（可选）" />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>
              保存设置
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
