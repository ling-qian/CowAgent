import { useState, useRef, useEffect } from 'react';
import { Input, Button, Card, Typography, Space, Tag, Spin, Empty, Tooltip } from 'antd';
import { SendOutlined, ClearOutlined, RobotOutlined, UserOutlined, DeleteOutlined } from '@ant-design/icons';
import { sendChatMessage, clearChatSession, getChatModels } from '../api/client';

const { TextArea } = Input;
const { Text, Title } = Typography;

export default function Chat() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [currentModel, setCurrentModel] = useState('');
  const messagesEndRef = useRef(null);

  useEffect(() => {
    // 加载当前模型信息
    getChatModels().then(res => {
      if (res.data?.current_model) {
        setCurrentModel(res.data.current_model);
      }
    }).catch(() => {});

    // 从 localStorage 恢复会话
    const saved = localStorage.getItem('chat_session_id');
    if (saved) setSessionId(saved);
    const savedMsgs = localStorage.getItem('chat_messages');
    if (savedMsgs) {
      try { setMessages(JSON.parse(savedMsgs)); } catch {}
    }
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    // 持久化消息
    if (messages.length > 0) {
      localStorage.setItem('chat_messages', JSON.stringify(messages));
    }
  }, [messages]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg = { role: 'user', content: text, ts: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const res = await sendChatMessage({
        message: text,
        session_id: sessionId || undefined,
      });

      const data = res.data;
      if (data.session_id && !sessionId) {
        setSessionId(data.session_id);
        localStorage.setItem('chat_session_id', data.session_id);
      }

      const assistantMsg = {
        role: 'assistant',
        content: data.content,
        mode: data.mode,
        ts: Date.now(),
      };
      setMessages(prev => [...prev, assistantMsg]);
    } catch (err) {
      const errorMsg = {
        role: 'assistant',
        content: `请求失败: ${err.response?.data?.error || err.message}`,
        mode: 'error',
        ts: Date.now(),
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleClear = async () => {
    if (sessionId) {
      try { await clearChatSession(sessionId); } catch {}
    }
    setMessages([]);
    setSessionId(null);
    localStorage.removeItem('chat_session_id');
    localStorage.removeItem('chat_messages');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const formatTime = (ts) => {
    return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 112px)' }}>
      {/* 顶部栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <Title level={4} style={{ margin: 0 }}>AI 对话</Title>
          {currentModel && <Tag color="blue">{currentModel}</Tag>}
          {sessionId && <Tag color="green">会话中</Tag>}
        </Space>
        <Tooltip title="清除会话">
          <Button icon={<ClearOutlined />} onClick={handleClear} danger>
            新对话
          </Button>
        </Tooltip>
      </div>

      {/* 消息区域 */}
      <Card
        style={{
          flex: 1,
          overflow: 'auto',
          marginBottom: 16,
          background: '#f5f5f5',
        }}
        bodyStyle={{ padding: 16 }}
      >
        {messages.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <span>
                <RobotOutlined style={{ fontSize: 32, color: '#1890ff', marginBottom: 8, display: 'block' }} />
                开始与 CowAgent 对话
              </span>
            }
          />
        ) : (
          messages.map((msg, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                marginBottom: 12,
              }}
            >
              <div
                style={{
                  maxWidth: '75%',
                  padding: '10px 14px',
                  borderRadius: 12,
                  background: msg.role === 'user' ? '#1890ff' : '#fff',
                  color: msg.role === 'user' ? '#fff' : '#333',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.1)',
                  position: 'relative',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  {msg.role === 'user' ? (
                    <UserOutlined style={{ fontSize: 12 }} />
                  ) : (
                    <RobotOutlined style={{ fontSize: 12 }} />
                  )}
                  <Text
                    style={{
                      fontSize: 11,
                      color: msg.role === 'user' ? 'rgba(255,255,255,0.7)' : '#999',
                    }}
                  >
                    {msg.role === 'user' ? '你' : 'CowAgent'}
                    {msg.mode === 'agent' && ' (Agent)'}
                    {' · '}
                    {formatTime(msg.ts)}
                  </Text>
                </div>
                <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, fontSize: 14 }}>
                  {msg.content}
                </div>
              </div>
            </div>
          ))
        )}
        {loading && (
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 12 }}>
            <div
              style={{
                padding: '10px 14px',
                borderRadius: 12,
                background: '#fff',
                boxShadow: '0 1px 2px rgba(0,0,0,0.1)',
              }}
            >
              <Spin size="small" /> <Text type="secondary">思考中...</Text>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </Card>

      {/* 输入区域 */}
      <div style={{ display: 'flex', gap: 8 }}>
        <TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          autoSize={{ minRows: 1, maxRows: 4 }}
          style={{ flex: 1 }}
          disabled={loading}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={handleSend}
          loading={loading}
          disabled={!input.trim()}
          style={{ height: 'auto' }}
        >
          发送
        </Button>
      </div>
    </div>
  );
}
