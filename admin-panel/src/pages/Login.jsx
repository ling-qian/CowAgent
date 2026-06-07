import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Form, Input, Button, Card, Typography, message, Space } from 'antd';
import { KeyOutlined } from '@ant-design/icons';
import { verifyApiKey } from '../api/client';

const { Title, Text } = Typography;

// 登录页面：通过输入API Key进行身份验证
export default function Login() {
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  // 提交登录表单：验证API Key有效性
  const handleSubmit = async (values) => {
    setLoading(true);
    try {
      await verifyApiKey(values.apiKey);
      // 验证成功，将API Key保存到localStorage
      localStorage.setItem('api_key', values.apiKey);
      message.success('登录成功');
      navigate('/dashboard');
    } catch (err) {
      // 验证失败，显示错误信息
      const errMsg = err.response?.data?.error || 'API Key 无效，请检查后重试';
      message.error(errMsg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 420 }} bordered={false}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div style={{ textAlign: 'center' }}>
            <Title level={2} style={{ marginBottom: 4 }}>
              CowAgent
            </Title>
            <Text type="secondary">SaaS 多租户管理面板</Text>
          </div>

          <Form onFinish={handleSubmit} layout="vertical" size="large">
            <Form.Item
              name="apiKey"
              label="API Key"
              rules={[{ required: true, message: '请输入 API Key' }]}
            >
              <Input.Password
                prefix={<KeyOutlined />}
                placeholder="请输入您的 API Key"
              />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" loading={loading} block>
                登录
              </Button>
            </Form.Item>
          </Form>
        </Space>
      </Card>
    </div>
  );
}
