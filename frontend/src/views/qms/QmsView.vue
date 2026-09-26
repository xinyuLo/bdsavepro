<template>
  <div class="qms-view">
    <div class="page-header">
      <h2>连接 QMediaSync</h2>
      <p class="page-desc">
        先在这里填好 QMediaSync 的地址和密钥，再到下面「连接列表」里，把你这边的一个任务和 QMS 的一个刮削目录绑起来。
        那个任务转存到新文件后，就会自动替你点对应的「启动」。
      </p>
    </div>

    <!-- 连接设置 -->
    <el-card class="qms-card">
      <template #header>
        <div class="card-head">
          <span>连接设置</span>
          <el-tag v-if="connState === 'ok'" type="success" size="small">已连接 · {{ connUser }}</el-tag>
          <el-tag v-else-if="connState === 'fail'" type="danger" size="small">连接失败</el-tag>
          <el-tag v-else type="info" size="small">未测试</el-tag>
        </div>
      </template>

      <el-form :model="form" label-width="120px">
        <el-form-item label="启用对接">
          <el-switch v-model="form.enabled" />
          <span class="form-help-inline">关闭后不会自动触发，手动按钮也置灰</span>
        </el-form-item>

        <el-form-item label="地址">
          <el-input v-model="form.host" placeholder="例如 192.168.1.100" style="max-width: 320px" />
        </el-form-item>

        <el-form-item label="端口">
          <el-input-number v-model="form.port" :min="1" :max="65535" :controls="false" style="width: 140px" />
          <span class="form-help-inline">默认 12333</span>
        </el-form-item>

        <el-form-item label="协议">
          <el-radio-group v-model="form.scheme">
            <el-radio-button label="http">http</el-radio-button>
            <el-radio-button label="https">https</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="认证方式">
          <el-radio-group v-model="form.auth_mode">
            <el-radio-button label="api_key">API Key（推荐）</el-radio-button>
            <el-radio-button label="password">账号密码</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="form.auth_mode === 'api_key'" label="API Key">
          <div class="key-row">
            <el-input
              :model-value="apiKeyValue"
              :readonly="!keyEditMode"
              :placeholder="keyEditMode ? '请输入 API Key' : ''"
              @input="onKeyInput"
            >
              <template v-if="!keyEditMode" #suffix>
                <el-icon
                  class="key-eye"
                  :title="showKey ? '隐藏' : '显示完整密钥'"
                  @mousedown.prevent
                  @click="toggleKey"
                >
                  <Hide v-if="showKey" />
                  <View v-else />
                </el-icon>
              </template>
            </el-input>
            <el-button
              v-if="!keyEditMode"
              size="small"
              @mousedown.prevent
              @click="startEditKey"
            >
              {{ config.has_api_key ? '更换' : '填写' }}
            </el-button>
            <el-button v-else size="small" @mousedown.prevent @click="cancelEditKey">取消</el-button>
          </div>
          <div class="form-help">
            在 QMediaSync「系统设置 → API 密钥」里自己创建一个，粘贴到这里保存即可（本工具不预置任何密钥）。
          </div>
        </el-form-item>

        <template v-else>
          <el-form-item label="用户名">
            <el-input v-model="form.username" placeholder="admin" style="max-width: 260px" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              :placeholder="config.has_password ? '已保存，留空则不改' : '登录 QMediaSync 的密码'"
              style="max-width: 260px"
            />
          </el-form-item>
        </template>

        <el-form-item label="自动触发">
          <el-switch v-model="form.auto_trigger" />
          <span class="form-help-inline">开了以后，下面连接列表里启用的连接才会在转存后自动触发</span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
          <el-button :loading="testing" @click="testConn">测试连接</el-button>
          <span v-if="testMessage" class="form-help-inline" :class="connState === 'ok' ? 'ok-text' : 'fail-text'">
            {{ testMessage }}
          </span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 连接列表 -->
    <el-card class="qms-card">
      <template #header>
        <div class="card-head">
          <span>连接列表（本工具任务 → QMS 刮削目录）</span>
          <div class="head-actions">
            <el-button size="small" :loading="loadingLinks" @click="loadLinks">刷新</el-button>
            <el-button size="small" type="success" plain :disabled="!form.enabled" :loading="startingAll" @click="startAll">
              立即触发全部
            </el-button>
            <el-button size="small" type="primary" @click="openCreate">创建连接</el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="links.length === 0" description="还没有连接。点「创建连接」，选一个任务 + 一个 QMS 刮削目录。" :image-size="80" />

      <el-table v-else :data="links" size="small" style="width: 100%">
        <el-table-column label="本工具任务" min-width="180">
          <template #default="{ row }">
            <span>{{ row.task_current_name || row.task_name || ('任务' + (row.task_order || '')) }}</span>
            <el-tag v-if="!row.task_exists" type="warning" size="small" class="mini-tag">任务已删除</el-tag>
            <el-tag v-else-if="!row.task_enabled" type="info" size="small" class="mini-tag">任务已停用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="QMS 刮削目录" min-width="260">
          <template #default="{ row }">
            <span class="qms-path">
              <b>#{{ row.qms_id }}</b>
              <span v-if="row.qms_media_type"> · {{ row.qms_media_type }}</span>
              <span v-if="row.qms_path"> · {{ row.qms_path }}</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ row.created_at || '-' }}</template>
        </el-table-column>
        <el-table-column label="自动触发" width="100">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" size="small" @change="(v: any) => toggleLink(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="185">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              :loading="triggeringId === row.id"
              :disabled="!form.enabled"
              @click="triggerOne(row)"
            >
              触发
            </el-button>
            <el-button link type="info" size="small" @click="openLogs(row)">日志</el-button>
            <el-button link type="danger" size="small" @click="removeLink(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-alert
        v-if="config.last_trigger_at"
        :type="config.last_trigger_ok === false ? 'warning' : 'success'"
        :closable="false"
        show-icon
        class="last-result"
      >
        <template #title>
          最近一次触发：{{ config.last_trigger_at }}
          <span v-if="config.last_trigger_task">（任务：{{ config.last_trigger_task }}）</span>
        </template>
        <div>{{ config.last_trigger_result || '-' }}</div>
      </el-alert>
    </el-card>

    <!-- 触发日志 -->
    <el-dialog v-model="logsVisible" :title="`触发日志 - ${logsTitle}`" width="720px" append-to-body>
      <div class="logs-toolbar">
        <span class="form-help-inline">只保留最近 30 条，超出自动清理</span>
        <el-button size="small" :loading="logsLoading" @click="loadLogs(logsLinkId)">刷新</el-button>
      </div>
      <el-empty v-if="!logsLoading && qmsLogs.length === 0" description="还没有触发记录" :image-size="70" />
      <el-table v-else :data="qmsLogs" size="small" style="width: 100%">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ row.trigger_at }}</template>
        </el-table-column>
        <el-table-column label="来源" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source === 'auto' ? 'primary' : 'info'" effect="light">
              {{ row.source === 'auto' ? '自动' : '手动' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.success ? 'success' : 'danger'" effect="light">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="消息" min-width="240">
          <template #default="{ row }">{{ row.message || '-' }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 创建连接 -->
    <el-dialog v-model="createVisible" title="创建连接" width="640px" append-to-body>
      <el-form label-width="140px">
        <el-form-item label="本工具的任务">
          <el-select
            v-model="createForm.task_ref"
            filterable
            placeholder="选择执行完后要触发刮削的任务"
            style="width: 100%"
          >
            <el-option
              v-for="t in tasks"
              :key="t.task_uid || t.order"
              :label="`${t.name || '未命名任务'}${t.enabled === false ? '（已停用）' : ''}`"
              :value="t.task_uid || t.order"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="QMS 刮削目录">
          <el-select
            v-model="createForm.qms_id"
            filterable
            placeholder="选择 QMediaSync 里的刮削任务（就是 #序号 那个）"
            style="width: 100%"
          >
            <el-option
              v-for="p in paths"
              :key="p.id"
              :label="`#${p.id} · ${p.media_type} · ${p.source_path}`"
              :value="p.id"
            />
          </el-select>
          <div class="form-help">
            列表来自 QMS 的 <code>/api/scrape/pathes</code>；如果为空先点下面的「刷新刮削目录」。
          </div>
        </el-form-item>

        <el-form-item>
          <el-button size="small" :loading="loadingPaths" @click="loadPaths">刷新刮削目录</el-button>
          <span class="form-help-inline">{{ paths.length }} 个刮削任务</span>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createLink">确认创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { View, Hide } from '@element-plus/icons-vue'
import { apiService } from '@/services/api'

interface QmsPath {
  id: number
  media_type: string
  source_path: string
  source_type: string
  scrape_type: string
  enable_cron: boolean
  cron_expression: string
}

interface QmsLink {
  id: string
  task_uid: string
  task_order: number | null
  task_name: string
  task_current_name?: string
  task_exists?: boolean
  task_enabled?: boolean
  qms_id: number | string
  qms_path: string
  qms_media_type: string
  enabled: boolean
  created_at: string
}

const form = reactive({
  enabled: false,
  scheme: 'http',
  host: '',
  port: 12333,
  auth_mode: 'api_key',
  api_key: '',
  username: '',
  password: '',
  auto_trigger: true
})

const config = ref<any>({})
const paths = ref<QmsPath[]>([])
const links = ref<QmsLink[]>([])
const tasks = ref<any[]>([])
const loadingPaths = ref(false)
const loadingLinks = ref(false)
const saving = ref(false)
const testing = ref(false)
const startingAll = ref(false)
const creating = ref(false)
const createVisible = ref(false)
const connState = ref<'none' | 'ok' | 'fail'>('none')
const connUser = ref('')
const testMessage = ref('')

const createForm = reactive<{ task_ref: any; qms_id: any }>({ task_ref: '', qms_id: '' })

// ---- API Key 显示与编辑（必须点「更换/填写」才进入编辑态，点输入框不会清空内容） ----
const keyEditMode = ref(false)
const showKey = ref(false)
const savedKey = ref('')

const maskedKey = computed(() => config.value.api_key_masked || '')

const apiKeyValue = computed(() => {
  if (keyEditMode.value) return form.api_key
  if (showKey.value) return savedKey.value || maskedKey.value
  return maskedKey.value
})

const startEditKey = () => {
  keyEditMode.value = true
  form.api_key = ''
  showKey.value = false
}

const cancelEditKey = () => {
  keyEditMode.value = false
  form.api_key = ''
  showKey.value = false
}

const onKeyInput = (v: string) => {
  form.api_key = v
}

const fetchSavedKey = async () => {
  if (savedKey.value) return true
  try {
    const res: any = await apiService.revealQmsKey()
    if (res.success) {
      savedKey.value = res.api_key || ''
      return true
    }
    ElMessage.error(res.message || '读取密钥失败')
  } catch {
    ElMessage.error('读取密钥失败')
  }
  return false
}

const toggleKey = async () => {
  if (showKey.value) {
    showKey.value = false
    return
  }
  if (!(await fetchSavedKey())) return
  showKey.value = true
}

const loadConfig = async () => {
  try {
    const res: any = await apiService.getQmsConfig()
    const c = res.config || {}
    config.value = c
    form.enabled = !!c.enabled
    form.scheme = c.scheme || 'http'
    form.host = c.host || ''
    form.port = c.port || 8020
    form.auth_mode = c.auth_mode || 'api_key'
    form.username = c.username || ''
    form.auto_trigger = c.auto_trigger !== false
    if (c.has_api_key) {
      fetchSavedKey()
    }
  } catch {
    ElMessage.error('读取 QMediaSync 配置失败')
  }
}

// 把页面当前填写的内容带上（这样不用先保存也能测试/刷新）
const currentConnFields = () => ({
  host: form.host,
  port: form.port,
  scheme: form.scheme,
  auth_mode: form.auth_mode,
  username: form.username,
  api_key: keyEditMode.value && form.api_key ? form.api_key : undefined,
  password: form.password || undefined
})

const loadPaths = async () => {
  loadingPaths.value = true
  try {
    const res: any = await apiService.getQmsPaths(currentConnFields())
    if (res.success) {
      paths.value = res.paths || []
    } else {
      ElMessage.error(res.message || '获取刮削目录失败')
    }
  } catch {
    ElMessage.error('获取刮削目录失败，先检查上面的连接设置')
  } finally {
    loadingPaths.value = false
  }
}

const loadLinks = async () => {
  loadingLinks.value = true
  try {
    const res: any = await apiService.getQmsLinks()
    if (res.success) {
      links.value = res.links || []
    } else {
      ElMessage.error(res.message || '读取连接列表失败')
    }
  } catch {
    ElMessage.error('读取连接列表失败')
  } finally {
    loadingLinks.value = false
  }
}

const loadTasks = async () => {
  try {
    const res: any = await apiService.getTasks()
    tasks.value = res.tasks || []
  } catch {
    tasks.value = []
  }
}

const save = async () => {
  saving.value = true
  try {
    const res: any = await apiService.saveQmsConfig({
      ...form,
      api_key: form.api_key || undefined,
      password: form.password || undefined,
      // 用户把已保存的密钥清空后点保存 → 明确告诉后端删除
      clear_api_key: keyEditMode.value && !form.api_key && !!config.value.has_api_key
    })
    if (res.success) {
      ElMessage.success(res.message || '已保存')
      form.api_key = ''
      form.password = ''
      keyEditMode.value = false
      showKey.value = false
      savedKey.value = ''
      await loadConfig()
    } else {
      ElMessage.error(res.message || '保存失败')
    }
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

const testConn = async () => {
  testing.value = true
  testMessage.value = ''
  try {
    const res: any = await apiService.testQms(currentConnFields())
    if (res.success) {
      connState.value = 'ok'
      connUser.value = res.username || ''
      testMessage.value = `连接成功，登录用户：${res.username || '未知'}`
    } else {
      connState.value = 'fail'
      testMessage.value = res.message || '连接失败'
    }
  } catch {
    connState.value = 'fail'
    testMessage.value = '连接失败'
  } finally {
    testing.value = false
  }
}

const openCreate = async () => {
  createForm.task_ref = ''
  createForm.qms_id = ''
  createVisible.value = true
  await Promise.all([loadTasks(), paths.value.length ? Promise.resolve() : loadPaths()])
}

const createLink = async () => {
  if (!createForm.task_ref) {
    ElMessage.warning('先选一个任务')
    return
  }
  if (createForm.qms_id === '' || createForm.qms_id === null) {
    ElMessage.warning('先选一个 QMS 刮削目录')
    return
  }
  creating.value = true
  try {
    const res: any = await apiService.createQmsLink({
      task_ref: createForm.task_ref,
      qms_id: createForm.qms_id
    })
    if (res.success) {
      ElMessage.success('连接已创建')
      createVisible.value = false
      await loadLinks()
    } else {
      ElMessage.error(res.message || '创建失败')
    }
  } catch {
    ElMessage.error('创建失败')
  } finally {
    creating.value = false
  }
}

const triggeringId = ref('')
const logsVisible = ref(false)
const logsLoading = ref(false)
const logsLinkId = ref('')
const logsTitle = ref('')
const qmsLogs = ref<any[]>([])

const openLogs = async (row: QmsLink) => {
  logsLinkId.value = row.id
  logsTitle.value = `${row.task_current_name || row.task_name || '任务'} → #${row.qms_id}`
  qmsLogs.value = []
  logsVisible.value = true
  await loadLogs(row.id)
}

const loadLogs = async (linkId: string) => {
  if (!linkId) return
  logsLoading.value = true
  try {
    const res: any = await apiService.getQmsLogs(linkId, 30)
    if (res.success) {
      qmsLogs.value = res.logs || []
    } else {
      ElMessage.error(res.message || '读取日志失败')
    }
  } catch {
    ElMessage.error('读取日志失败')
  } finally {
    logsLoading.value = false
  }
}

const triggerOne = async (row: QmsLink) => {
  const qid = Number(row.qms_id)
  if (!qid || Number.isNaN(qid)) {
    ElMessage.warning('这条连接没有有效的刮削序号')
    return
  }
  triggeringId.value = row.id
  try {
    const res: any = await apiService.triggerQmsLink(row.id)
    if (res.success) {
      ElMessage.success(res.message || `已触发 #${qid}`)
    } else {
      ElMessage.error(res.message || '触发失败')
    }
    await loadConfig()
  } catch {
    ElMessage.error('触发失败')
  } finally {
    triggeringId.value = ''
  }
}

const toggleLink = async (row: QmsLink, value: any) => {
  try {
    const res: any = await apiService.toggleQmsLink(row.id, !!value)
    if (res.success) {
      row.enabled = !!value
      ElMessage.success(value ? '已启用该连接' : '已停用该连接')
    } else {
      ElMessage.error(res.message || '更新失败')
    }
  } catch {
    ElMessage.error('更新失败')
  }
}

const removeLink = async (row: QmsLink) => {
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.task_current_name || row.task_name || '任务'} → #${row.qms_id}」这条连接吗？`,
      '删除连接',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res: any = await apiService.deleteQmsLink(row.id)
    if (res.success) {
      ElMessage.success('已删除')
      await loadLinks()
    } else {
      ElMessage.error(res.message || '删除失败')
    }
  } catch {
    ElMessage.error('删除失败')
  }
}

const startAll = async () => {
  const ids = links.value.filter((l) => l.enabled).map((l) => Number(l.qms_id)).filter((n) => !Number.isNaN(n))
  if (!ids.length) {
    ElMessage.warning('没有启用的连接')
    return
  }
  try {
    await ElMessageBox.confirm(
      `会在 QMediaSync 里触发这些刮削任务：${ids.map((i) => '#' + i).join('、')}`,
      '立即触发',
      { type: 'warning', confirmButtonText: '触发', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  startingAll.value = true
  try {
    const res: any = await apiService.startQms({ ids })
    if (res.success) {
      ElMessage.success(res.message || '已触发')
    } else {
      ElMessage.error(res.message || '触发失败')
    }
    await loadConfig()
  } catch {
    ElMessage.error('触发失败')
  } finally {
    startingAll.value = false
  }
}

onMounted(async () => {
  await loadConfig()
  await loadLinks()
  if (form.host) {
    testConn()
    loadPaths()
  }
})
</script>

<style scoped>
.qms-view {
  padding: 4px;
}

.page-header {
  margin-bottom: 16px;
}

.page-header h2 {
  margin: 0 0 6px;
  font-size: 20px;
  color: #303133;
}

.page-desc {
  margin: 0;
  font-size: 13px;
  color: #909399;
  line-height: 1.7;
}

.qms-card {
  margin-bottom: 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
  flex-wrap: wrap;
}

.head-actions {
  display: flex;
  gap: 8px;
}

.form-help {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

.key-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 560px;
}

.key-row .el-input {
  flex: 1;
}

.key-eye {
  cursor: pointer;
  color: #909399;
}

.key-eye:hover {
  color: #409eff;
}

.form-help-inline {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
}

.ok-text {
  color: #67c23a;
}

.fail-text {
  color: #f56c6c;
}

.qms-path {
  font-size: 13px;
  color: #409eff;
  word-break: break-all;
}

.mini-tag {
  margin-left: 6px;
}

.last-result {
  margin-top: 14px;
}

code {
  background: #f4f4f5;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
}
</style>
