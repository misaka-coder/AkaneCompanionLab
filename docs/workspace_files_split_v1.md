# Workspace file service 拆分记录

目标：把 `companion_v01/workspace_files.py` 的路径、目录、正文读取、分页及 Focus/上下文按职责分开，保留工具层入口与安全约束。仅修改本轮工作区文件服务及其测试、说明。

## 切片 1：路径与目录

- 原入口 1314 行，切片 1 后为 738 行。
- `workspace_files_runtime/definitions.py`：文件类型常量、路径/读取异常、解析结果类型；原入口继续导出。
- `paths.py`：根目录/固定分层、相对 URI、安全校验和目标去重。
- `traversal.py`：实时能力探测与目录遍历，不读取正文。
- `listing.py`：目录查询、文件状态描述与最近文件元数据。
- `listing_paging.py`：列表分页与完整条目渲染。
- `targets.py`：批量文件/目录目标解析。
- 删除前一份被后续同名定义覆盖的 `_read_file`；两份方法的 AST 完全一致。
- 33 个有效方法的参数、返回注解和装饰器保持；此切片仅调整职责归属与相对导入。

基线与切片 1 后的检查均为 92 项，91 通过，1 项因当前 Windows 环境无法创建符号链接而跳过。覆盖工作区查询/读取/分页/注册、Focus、桌宠工作台、附件和资源可见性集成。AST 签名/有效正文审计、Python 编译、`git diff --check` 均通过。

## 切片 2：正文、分页与临时快照

- 入口继续降至 285 行；正文和页生成移至 `reading.py`、`documents.py`、`file_paging.py`。
- `reading.py` 合并普通读取/分页读取的重复格式分发和失败处理；普通文本预览按字符预算读取有限字节前缀，保留 UTF-8、GB18030 和换行内容。
- `documents.py` 用一套增量字符计数生成 Word/Excel/PDF/ZIP 窗口；只保留预算外 1 个探测字符，避免单段/单行撑破窗口或恰好达到预算时误报截断。Excel 工作簿始终在 `finally` 中关闭。
- `page_source.py` 在每次读取前流式校验源文件；首次提取后再次校验，拒绝读取期间发生变化或消失的旧正文。
- `read_cache.py` 提供实例内快照，按用户、会话、相对 URI 和字节限制隔离，最多 8 项/合计 8M 字符，300 秒过期。正文和来源校验值不进入快照 `repr`，不写磁盘或后台任务。
- 未变化且快照仍在缓存中的续页只提取一次正文；淘汰、过期、超出缓存容量或服务重新创建后，重新提取并校验同一正文指纹，原有游标可继续使用。
- `p1.ws`、用户/会话绑定、原 `t/i/o/f` 字段和正文指纹规则保留；增加可选 `c` 字段跨文件记住窗口截断，旧游标未带此字段时仍可继续。
- 提取窗口耗尽但原文未覆盖时，整体及单文件 `complete` 均为假；不再制造指向结束位置的无效续页。多文件页剩余空间不足时留到下一页，避免最小切片大小撑破剩余预算。
- 无文本层 PDF 返回真实读取失败，不把空正文当作已读取。

边界：默认仍为 64 MiB 源文件限制；普通文本分页可读取该字节限制内的全文，文档/ZIP 保留 4M 字符提取窗口。续页仍会流式扫描源字节来验证来源，优化减少重复结构解析、解码和正文指纹计算；并不免除全部磁盘 I/O。第三方 Word/PDF 解析器自身的结构加载成本仍存在。

验证：新增用例先在旧实现复现重复提取、窗口/完成状态和读取期间来源变化等问题。切片 2 共 116 项检查，115 通过、1 符号链接环境跳过；包括真实 UTF-8/GB18030 文本、CRLF、Word、多个 Excel sheet、真实 PDF 多页和空正文、缓存容量/过期/淘汰/重启、旧游标、来源修改/删除/验证失败、工具层回执。33 个原方法签名、Python 编译与 `git diff --check` 通过。

## 切片 3：Focus 与上下文收尾

- `focus.py` 保留用户/会话内的 Focus 状态操作，`context.py` 保留工作区概览、最近文件、持续加载文件、修改时间及失败/截断说明。
- 入口最终为 67 行，只负责兼容导出、根目录选择、实例初始化和职责组合；13 个实现模块最大为 `file_paging.py` 的 213 行。
- 替换 Focus 的非空目标全部不存在或被拒绝时，返回失败并保留原来的 Focus。显式空列表、实际存在的空目录仍可用于清空 Focus，避免改变原有清空语义。
- 同一文件的多种路径写法只在 `affected` 中出现一次。
- `ReadWorkspaceToolHandler` 的结束回执引用逐项状态，避免失败/无文本 PDF 仍被结尾文字说成“已读取本次目标范围”；工具参数、事件和 envelope 接口不变。
- 原 33 个方法签名、返回注解、装饰器和文件类型常量保持；除列出的读取边界和 Focus 修复，其余方法正文仅调整职责归属和导入。

Focus 新用例先复现 3 个失败替换/别名问题后再修复；覆盖空态、用户/会话切换、文件删除、重新创建服务和截断提示。工具回执用例先复现无正文 PDF 仍被结尾描述为已读完，再修正结束措辞。

## 最终验证

- 主检查 125 项：124 通过、1 符号链接环境跳过。模块为 `test_workspace_focus_context`、`test_workspace_document_readers`、`test_workspace_read_snapshots`、`test_workspace_files`、`test_workspace_paged_reading`、`test_workspace_management`、`test_desktop_workspace_panel`、`test_long_tool_result_repair`、`test_resource_visibility_contract`、`test_attachment_inbox`。
- 相邻链路 147 项全部通过：`test_attachment_ingest`、`test_attachment_ingest_runtime`、`test_execution_resources`、`test_generated_files`、`test_desktop_current_attachments`、`test_attachment_section_paging`、`test_attachment_inbox_runtime`。
- 合计 **272 项有效检查，271 通过、1 跳过，无功能失败**。33 个有效方法签名及原文件类型常量一致；Python 编译、静态导入关系检查（无循环、无实现模块反向导入入口）、`git diff --check` 均通过。
- 原 `workspace_files.py` 中重复 `_read_file` 已删除；普通/分页读取共用一次格式分发。原调用入口仍用于引擎初始化、服务工厂、目录工具、读取工具、工作区文件注册以及附件/生成文件的路径解析。
- 其他并行任务的配置、记忆、QQ、语音、引擎和前端改动保留；本轮仅 stage 工作区服务、相关工具回执、3 个新增行为测试文件及本文。

真实桌宠/QQ 操作与外部服务验收不属于本轮自动化检查结果；没有修改前端资源。真实 PDF 测试使用本地构造的普通文本/空白 PDF，不能覆盖所有扫描件、加密或损坏 PDF。
