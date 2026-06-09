import axios from 'axios';

// 创建axios实例，配置基础URL和超时时间
const client = axios.create({
  baseURL: '/api',
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器：从localStorage读取API Key并注入到请求头
client.interceptors.request.use((config) => {
  const apiKey = localStorage.getItem('api_key');
  if (apiKey) {
    config.headers['X-API-Key'] = apiKey;
  }
  return config;
});

// 响应拦截器：处理401未授权错误，清除本地凭证并跳转到登录页
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('api_key');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// --- 认证相关 ---

// 验证API Key是否有效
export async function verifyApiKey(key) {
  return client.get('/tenants/me/stats', {
    headers: { 'X-API-Key': key },
  });
}

// --- 租户相关 ---

// 获取当前租户统计信息
export async function getTenantStats() {
  return client.get('/tenants/me/stats');
}

// 获取当前租户信息
export async function getTenantInfo() {
  return client.get('/tenants/me');
}

// 更新当前租户配置
export async function updateTenant(data) {
  return client.put('/tenants/me', data);
}

// --- API密钥相关 ---

// 获取所有API密钥
export async function getApiKeys() {
  return client.get('/keys');
}

// 创建新的API密钥
export async function createApiKey(data) {
  return client.post('/keys', data);
}

// 撤销（删除）指定API密钥
export async function revokeApiKey(keyId) {
  return client.delete(`/keys/${keyId}`);
}

// --- 用量相关 ---

// 获取用量记录
export async function getUsage(params) {
  return client.get('/usage', { params });
}

// --- IM 渠道相关 ---

// 获取 IM 渠道映射列表
export async function getImChannels() {
  return client.get('/im-channels');
}

// 创建 IM 渠道映射
export async function createImChannel(data) {
  return client.post('/im-channels', data);
}

// 删除 IM 渠道映射
export async function deleteImChannel(mappingId) {
  return client.delete(`/im-channels/${mappingId}`);
}

// --- Webhook 相关 ---

// 获取 Webhook 列表
export async function getWebhooks() {
  return client.get('/webhooks');
}

// 创建 Webhook
export async function createWebhook(data) {
  return client.post('/webhooks', data);
}

// 删除 Webhook
export async function deleteWebhook(webhookId) {
  return client.delete(`/webhooks/${webhookId}`);
}

// --- 审计日志相关 ---

// 获取审计日志
export async function getAuditLogs(params) {
  return client.get('/audit', { params });
}

// --- 对话相关 ---

// 发送消息（非流式）
export async function sendChatMessage({ message, session_id, system_prompt }) {
  return client.post('/chat/completions', { message, session_id, system_prompt });
}

// 获取会话列表
export async function getChatSessions() {
  return client.get('/chat/sessions');
}

// 清除会话
export async function clearChatSession(sessionId) {
  return client.delete(`/chat/sessions/${sessionId}`);
}

// 获取可用模型
export async function getChatModels() {
  return client.get('/chat/models');
}

export default client;
