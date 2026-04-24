// xms 前端逻辑 - 重构版
const { createApp, ref, computed, onMounted } = Vue;
const API = '/api';

createApp({
  setup() {
    // 登录状态
    const loggedIn = ref(false);
    const loginTab = ref('qrcode');
    const qrcode = ref('');
    const qrcodeStatus = ref('');
    const qrcodeDeviceCode = ref('');
    const phone = ref('');
    const smsCode = ref('');
    const username = ref('');
    const verificationToken = ref('');
    const stepMsg = ref('');
    const stepMsgType = ref('info');
    const smsBtnText = ref('发送验证码');
    const sendingSMS = ref(false);
    const userInfo = ref({});

    // 主视图
    const view = ref('dashboard');
    const fileList = ref([]);
    const loading = ref(false);
    const currentDirId = ref(null);
    const pathStack = ref([]);
    const hasMore = ref(false);
    const page = ref(0);

    // STRM
    const syncing = ref(false);
    const syncProgress = ref(0);
    const syncProgressMsg = ref('');
    const syncConfig = ref({ parentId: '', depth: 3, deleteSync: true, refreshLinks: true });
    const syncDirs = ref([]);
    const cacheCount = ref(0);
    const logs = ref([]);
    const strmStats = ref({ count: 0, lastSync: '从未' });

    // 定时任务
    const tasks = ref([]);

    // Webhook
    const webhooks = ref([]);

    // 插件
    const pluginTab = ref('rename');
    const pluginList = ref([]);

    // 设置
    const cfg = ref({ strmDir: '/app/media/strm', mediaRoot: '/app/media', tmdbKey: '' });

    // 播放器
    const playerVisible = ref(false);
    const playerUrl = ref('');

    // 统计数据
    const stats = computed(() => ({
      strmCount: strmStats.value.count,
      fileCount: fileList.value.length,
      taskCount: tasks.value.length,
      cacheCount: cacheCount.value,
    }));

    // ===== 登录 =====
    async function checkAuth() {
      try {
        const r = await axios.get(API + '/auth/status');
        if (r.data.logged_in) { loggedIn.value = true; userInfo.value = r.data.user || {}; }
      } catch (e) {}
    }

    async function generateQRCode() {
      try {
        const r = await axios.post(API + '/auth/qrcode/generate');
        if (r.data.qrcode_url) { qrcode.value = r.data.qrcode_url; qrcodeDeviceCode.value = r.data.device_code; pollQRCode(); }
      } catch (e) { qrcodeStatus.value = { type: 'error', msg: '生成失败' }; }
    }

    async function pollQRCode() {
      if (!qrcodeDeviceCode.value) return;
      const check = async () => {
        try {
          const r = await axios.post(API + '/auth/qrcode/check', { device_code: qrcodeDeviceCode.value });
          if (r.data.access_token) { qrcodeStatus.value = { type: 'success', msg: '登录成功！' }; loggedIn.value = true; userInfo.value = r.data.user || {}; loadDashboard(); }
          else if (r.data.pending) { qrcodeStatus.value = { type: 'info', msg: '等待扫码...' }; setTimeout(check, 2000); }
          else { qrcodeStatus.value = { type: 'error', msg: r.data.error || '扫码超时' }; }
        } catch (e) { setTimeout(check, 3000); }
      };
      setTimeout(check, 2000);
    }

    async function sendSMS() {
      if (!phone.value || phone.value.length < 11) return;
      sendingSMS.value = true;
      try {
        const r = await axios.post(API + '/auth/phone/send_code', { phone: phone.value });
        if (r.data.verification_id) { verificationToken.value = r.data.verification_token || ''; stepMsg.value = '验证码已发送，请查收'; stepMsgType.value = 'success'; }
        else { stepMsg.value = r.data.error || '发送失败'; stepMsgType.value = 'error'; }
      } catch (e) { stepMsg.value = '发送失败'; stepMsgType.value = 'error'; }
      sendingSMS.value = false;
    }

    async function signin() {
      try {
        const r = await axios.post(API + '/auth/phone/signin', { verification_code: smsCode.value, verification_token: verificationToken.value, username: username.value, captcha_token: '' });
        if (r.data.success) { loggedIn.value = true; loadDashboard(); }
        else { stepMsg.value = '登录失败'; stepMsgType.value = 'error'; }
      } catch (e) { stepMsg.value = '登录失败'; stepMsgType.value = 'error'; }
    }

    function logout() { loggedIn.value = false; userInfo.value = {}; fileList.value = []; }

    // ===== 文件 =====
    async function loadFiles(parentId) {
      loading.value = true;
      page.value = 0;
      try {
        const r = await axios.get(API + '/files', { params: { parent_id: parentId, page: 0, page_size: 50 } });
        fileList.value = r.data.data?.list || [];
        currentDirId.value = parentId;
        hasMore.value = (r.data.data?.list || []).length === 50;
        if (parentId === null) pathStack.value = [];
      } catch (e) { fileList.value = []; }
      loading.value = false;
    }

    function enterDir(item) {
      pathStack.value.push({ id: currentDirId.value, name: item.fileName || item.name });
      loadFiles(item.fileId || item.id);
    }

    function goBackDir() {
      if (!pathStack.value.length) return;
      const prev = pathStack.value.pop();
      loadFiles(prev.id);
    }

    function jumpToPath(index) {
      pathStack.value = pathStack.value.slice(0, index);
      loadFiles(index === 0 ? null : pathStack.value[index - 1]?.id);
    }

    async function loadMore() {
      page.value++;
      try {
        const r = await axios.get(API + '/files', { params: { parent_id: currentDirId.value, page: page.value, page_size: 50 } });
        const list = r.data.data?.list || [];
        fileList.value.push(...list);
        hasMore.value = list.length === 50;
      } catch (e) {}
    }

    function getIcon(item) {
      const n = (item.fileName || item.name || '').toLowerCase();
      if (n.endsWith('.mp4') || n.endsWith('.mkv') || n.endsWith('.avi') || n.endsWith('.mov') || n.endsWith('.ts')) return '🎬';
      if (n.endsWith('.jpg') || n.endsWith('.png') || n.endsWith('.gif') || n.endsWith('.webp')) return '🖼️';
      return '📄';
    }

    function formatSize(b) {
      if (!b) return '';
      const u = ['B', 'KB', 'MB', 'GB'];
      let i = 0;
      while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
      return b.toFixed(1) + ' ' + u[i];
    }

    function previewFile(item) { playerUrl.value = '/embed/' + (item.fileId || item.id); playerVisible.value = true; }

    async function genSTRM(item) {
      try {
        await axios.post(API + '/strm/refresh', { file_id: item.fileId || item.id, file_path: item.fileName || item.name });
        addLog('success', `已生成STRM: ${item.fileName || item.name}`);
      } catch (e) { addLog('error', `STRM生成失败: ${item.fileName || item.name}`); }
    }

    // ===== STRM =====
    async function startSync() {
      syncing.value = true;
      syncProgress.value = 10;
      syncProgressMsg.value = '开始同步...';
      addLog('info', 'STRM 同步开始');
      try {
        const r = await axios.post(API + '/strm/sync', { parent_id: syncConfig.value.parentId || null, folder_path: '', depth: parseInt(syncConfig.value.depth) });
        syncProgress.value = 100;
        syncProgressMsg.value = '同步完成';
        addLog('success', `同步完成: ${r.data.success || 0} 文件`);
        await loadStrmStatus();
      } catch (e) { addLog('error', '同步失败'); }
      syncing.value = false;
      setTimeout(() => { syncProgress.value = 0; }, 2000);
    }

    async function loadStrmStatus() {
      try {
        const r = await axios.get(API + '/strm/status');
        strmStats.value = r.data;
        cacheCount.value = r.data.cache_count || 0;
      } catch (e) {}
    }

    async function clearCache() {
      await axios.post(API + '/cache/clear');
      cacheCount.value = 0;
      addLog('success', '缓存已清空');
    }

    function startSyncNow() { view.value = 'strm'; startSync(); }

    // ===== 定时任务 =====
    async function loadTasks() {
      try {
        const r = await axios.get(API + '/scheduler/tasks');
        tasks.value = r.data.tasks || [];
      } catch (e) { tasks.value = []; }
    }

    async function toggleTask(task) {
      await axios.post(API + '/scheduler/tasks/' + task.id + '/toggle', { enabled: task.enabled });
    }

    async function runTaskNow(task) {
      await axios.post(API + '/scheduler/tasks/' + task.id + '/run');
      addLog('info', `任务 "${task.name}" 已触发`);
    }

    async function deleteTask(task) {
      await axios.delete(API + '/scheduler/tasks/' + task.id);
      tasks.value = tasks.value.filter(t => t.id !== task.id);
    }

    function openAddTask() { /* 弹窗逻辑 */ }

    // ===== Webhook =====
    async function loadWebhooks() {
      try {
        const r = await axios.get(API + '/webhook/list');
        webhooks.value = r.data.webhooks || [];
      } catch (e) { webhooks.value = []; }
    }

    async function deleteWebhook(wh) {
      await axios.delete(API + '/webhook/' + wh.id);
      webhooks.value = webhooks.value.filter(w => w.id !== wh.id);
    }

    function openAddWebhook() {}

    // ===== 插件 =====
    async function loadPlugins() {
      try {
        const r = await axios.get(API + '/plugin/list');
        pluginList.value = r.data.plugins || [];
      } catch (e) { pluginList.value = []; }
    }

    async function deletePlugin(pl) {
      await axios.delete(API + '/plugin/' + pl.id);
      pluginList.value = pluginList.value.filter(p => p.id !== pl.id);
    }

    function openAddPlugin() {}
    const filteredPlugins = computed(() => pluginList.value.filter(p => p.type === pluginTab.value));

    // ===== 设置 =====
    async function loadConfig() {
      try {
        const r = await axios.get(API + '/config');
        cfg.value = { ...cfg.value, ...r.data };
      } catch (e) {}
    }

    async function saveSettings() {
      await axios.post(API + '/config', cfg.value);
      addLog('success', '设置已保存');
    }

    // ===== 日志 =====
    function addLog(level, msg) {
      const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
      logs.value.push({ time: t, level, msg });
      if (logs.value.length > 100) logs.value.shift();
    }

    function loadLogs() { /* 从 localStorage 读取或 API */ }

    function formatTime(ts) {
      if (!ts) return '-';
      return new Date(ts * 1000).toLocaleString('zh-CN');
    }

    // ===== Dashboard =====
    async function loadDashboard() {
      await Promise.all([loadStrmStatus(), loadTasks(), loadWebhooks(), loadPlugins(), loadConfig()]);
    }

    const pluginListFiltered = computed(() => pluginList.value.filter(p => p.type === pluginTab.value));

    onMounted(async () => {
      await checkAuth();
      if (loggedIn.value) { loadDashboard(); }
      else if (loginTab.value === 'qrcode') { generateQRCode(); }
    });

    return {
      loggedIn, loginTab, qrcode, qrcodeStatus, phone, smsCode, username, verificationToken,
      stepMsg, stepMsgType, smsBtnText, sendingSMS, userInfo,
      view, fileList, loading, currentDirId, pathStack, hasMore, page,
      syncing, syncProgress, syncProgressMsg, syncConfig, syncDirs, cacheCount, logs, strmStats,
      tasks, webhooks, pluginTab, pluginList: pluginListFiltered, cfg,
      playerVisible, playerUrl, stats,
      generateQRCode, logout, sendSMS, signin,
      loadFiles, enterDir, goBackDir, jumpToPath, loadMore, getIcon, formatSize, previewFile, genSTRM,
      startSync, loadStrmStatus, clearCache, startSyncNow,
      loadTasks, toggleTask, runTaskNow, deleteTask, openAddTask,
      loadWebhooks, deleteWebhook, openAddWebhook,
      loadPlugins, deletePlugin, openAddPlugin,
      loadConfig, saveSettings, addLog, loadLogs, formatTime,
    };
  }
}).mount('#app');
