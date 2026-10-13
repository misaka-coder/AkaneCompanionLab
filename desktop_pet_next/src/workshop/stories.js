import { buildText, setFieldValue, flashElement } from "./dom.js";
import { formatError } from "./shared.js";

const AI_STORY_PROMPT = `你是一个沉浸式桌宠互动剧本编剧。请根据我给定的主题与角色设定，编写一份符合 AkaneCompanionLab 互动剧本规范的 Markdown 格式剧本。

【剧本格式规范】：
必须以 YAML Frontmatter 开头：
---
story_id: <唯一ID，英文小写与下划线，例如 afternoon_tea>
title: <剧本标题>
description: <一句话剧本简介>
cover_image: scenes/家/白天客厅.png
initial_node_id: intro_1
---

节点格式：每个节点以 ## 节点ID [类型] 开头。完整支持 4 种类型：
1. [script]：剧情台词
   角色: Akane
   表情: normal (支持 normal/开心/卖萌/脸红/思考中/气鼓鼓/得意 等)
   动作: nod (支持 nod/shake/bounce/idle 等)
   背景: scenes/家/白天客厅.png
   下一幕: 下一个节点ID
   正文台词内容...

2. [choice]：分支选择
   标题: 选择肢提示
   角色: Akane
   表情: 卖萌
   动作: idle
   提示台词...
   ? 你的选择:
   - "选项A文案" -> target_node_A
   - "选项B文案" -> target_node_B
   - （自由对话）自由输入提示 -> agent_node

3. [agent]：自由对话交谈
   角色: Akane
   表情: 思考中
   动作: nod
   目标: 角色在此处与玩家自由互动的目标引导说明
   下一幕: 结算节点ID
   正文引导台词...

4. [ending]：结局结算
   结局标题: 结局一：标题
   结局总结: 本次故事的结局评语
   表情: 开心
   动作: bounce
   结局总结台词...

请保持剧情温馨治愈，角色性格鲜活生动。不要输出任何 Markdown 剧本代码块以外的额外解释废话。`;

export function createStoryController({
  els,
  findPack,
  isTauriRuntime,
  invoke,
  setStatus,
  view,
  storiesState,
  writeState,
  isPackWriteBusy,
  updateCatalogControls,
  navigationState,
}) {
  let storyReadRequestToken = 0;

  function showStoriesEmpty() {
    if (els.storiesContent) els.storiesContent.hidden = true;
    if (els.storiesEmptyState) els.storiesEmptyState.hidden = false;
  }

  async function loadStoriesTab(packId) {
    const requestToken = ++storyReadRequestToken;
    const visit = navigationState.workspaceVisit;
    const current = () => requestToken === storyReadRequestToken && visit === navigationState.workspaceVisit
      && view.activeTab === "stories" && view.workspacePackId === packId && Boolean(findPack(packId));
    const pack = findPack(packId);
    if (!pack) {
      showStoriesEmpty();
      return;
    }
    if (els.storiesEmptyState) els.storiesEmptyState.hidden = true;
    if (els.storiesContent) els.storiesContent.hidden = false;
    if (!els.storiesList) return;

    els.storiesList.replaceChildren(buildText("p", "正在扫描剧本列表…"));

    if (!isTauriRuntime) {
      els.storiesList.replaceChildren(buildText("p", "浏览器预览模式不支持读取本地剧本。"));
      return;
    }

    try {
      const stories = await invoke("list_character_stories", { packId });
      if (!current()) return false;
      renderStoriesList(stories, packId);
      return true;
    } catch (error) {
      if (current()) els.storiesList.replaceChildren(buildText("p", `读取剧本失败：${formatError(error)}`));
      return false;
    }
  }

  function renderStoriesList(stories, packId) {
    if (!els.storiesList) return;
    els.storiesList.replaceChildren();

    if (!Array.isArray(stories) || stories.length === 0) {
      const empty = document.createElement("div");
      empty.className = "story-card story-card-empty";
      const title = document.createElement("strong");
      title.textContent = "还没有互动剧本";
      const note = document.createElement("p");
      note.textContent = "点击上方「新建剧本草稿」，或点击「打开剧本目录」将写好的 .md 剧本放入 stories/ 文件夹中。";
      empty.append(title, note);
      els.storiesList.appendChild(empty);
      return;
    }

    for (const item of stories) {
      const card = document.createElement("article");
      card.className = "story-card";

      const header = document.createElement("div");
      header.className = "story-card-header";

      const titleEl = document.createElement("div");
      titleEl.className = "story-card-title";
      titleEl.textContent = item.title || item.story_id || item.file_name;

      const badge = document.createElement("span");
      badge.className = "story-card-badge";
      badge.textContent = item.file_name;
      header.append(titleEl, badge);

      const desc = document.createElement("p");
      desc.className = "story-card-desc";
      desc.textContent = item.description || "暂无简介说明";

      const footer = document.createElement("div");
      footer.className = "story-card-footer";

      const sizeKb = (item.size_bytes / 1024).toFixed(1);
      const meta = document.createElement("span");
      meta.textContent = `${sizeKb} KB · ID: ${item.story_id}`;

      const actions = document.createElement("div");
      actions.className = "story-card-actions";

      const openBtn = document.createElement("button");
      openBtn.type = "button";
      openBtn.className = "btn-secondary";
      openBtn.textContent = "编辑剧本";
      openBtn.addEventListener("click", async () => {
        try {
          await invoke("open_local_file", { path: item.path });
        } catch (err) {
          setStatus(`打开文件失败：${formatError(err)}`);
        }
      });

      const revealBtn = document.createElement("button");
      revealBtn.type = "button";
      revealBtn.className = "btn-secondary";
      revealBtn.textContent = "在目录中查看";
      revealBtn.addEventListener("click", async () => {
        try {
          await invoke("show_item_in_folder", { path: item.path });
        } catch (err) {
          setStatus(`打开目录失败：${formatError(err)}`);
        }
      });

      actions.append(openBtn, revealBtn);
      footer.append(meta, actions);
      card.append(header, desc, footer);
      els.storiesList.appendChild(card);
    }
  }

  async function openStoriesFolder() {
    const packId = String(view.workspacePackId || view.activePackId || "").trim();
    if (!packId) {
      setStatus("请先选择一个角色包。");
      return;
    }
    if (!isTauriRuntime) {
      setStatus("浏览器预览模式不支持打开本地目录。");
      return;
    }
    try {
      await invoke("open_character_stories_folder", { packId });
      setStatus("已在资源管理器中打开角色 stories 目录。");
    } catch (error) {
      setStatus(`打开目录失败：${formatError(error)}`);
    }
  }

  async function copyStoryPrompt() {
    try {
      await navigator.clipboard.writeText(AI_STORY_PROMPT);
      if (els.storiesStatus) {
        els.storiesStatus.textContent = "已复制 AI 创作提示词！直接发送给 DeepSeek、Claude 或 ChatGPT 即可生成小剧场。";
      }
      setStatus("已复制 AI 创作提示词！");
    } catch (error) {
      setStatus(`复制失败：${formatError(error)}`);
    }
  }

  function openStoryDialog() {
    const packId = String(view.workspacePackId || view.activePackId || "").trim();
    if (!packId) {
      setStatus("请先选择一个角色包。");
      return;
    }
    if (!isTauriRuntime) {
      setStatus("浏览器预览模式不支持创建剧本。");
      return;
    }
    storiesState.storyDialogTarget = { packId, visit: navigationState.workspaceVisit };
    setFieldValue(els.storyTitleInput, "");
    setFieldValue(els.storyIdInput, "");
    showStoryError("");
    updateCatalogControls();
    els.storyDialog?.showModal();
  }

  function showStoryError(message) {
    if (!els.storyDialogError) return;
    els.storyDialogError.textContent = String(message || "");
    els.storyDialogError.hidden = !message;
    if (message) flashElement(els.storyForm, "invalid-flash");
  }

  async function createStory() {
    const target = storiesState.storyDialogTarget;
    if (!target || !els.storyDialog?.open || !findPack(target.packId)) return;
    const { packId } = target;
    if (isPackWriteBusy(packId)) return { ok: false, reason: "operation-in-progress" };
    const title = String(els.storyTitleInput?.value || "").trim();
    const storyId = String(els.storyIdInput?.value || "").trim();

    if (!title) {
      showStoryError("请填写剧本标题。");
      return;
    }
    if (!storyId) {
      showStoryError("请填写剧本 ID (英文/数字/下划线)。");
      return;
    }

    const ownsPage = () => view.activeTab === "stories" && view.workspacePackId === packId
      && navigationState.workspaceVisit === target.visit && Boolean(findPack(packId));
    const ownsDialog = () => ownsPage() && storiesState.storyDialogTarget === target && els.storyDialog.open;
    writeState.storySaves.add(packId);
    ++writeState.registryRevision;
    updateCatalogControls();
    try {
      const filePath = await invoke("create_character_story_template", {
        packId,
        storyId,
        title,
      });
      if (typeof filePath !== "string" || !filePath.trim()) {
        throw new Error("剧本创建返回了无效结果，请检查角色剧本目录。");
      }
      if (ownsDialog()) els.storyDialog.close();
      if (ownsPage()) {
        setStatus(`已成功创建剧本：${title}`);
        await loadStoriesTab(packId);
        if (ownsPage()) {
          try {
            await invoke("open_local_file", { path: filePath });
          } catch {
            /* opening editor is best effort */
          }
        }
      }
      return { ok: true, packId };
    } catch (error) {
      const message = formatError(error);
      if (ownsDialog()) showStoryError(message);
      else if (ownsPage()) setStatus(`创建剧本失败：${message}`);
      return { ok: false, packId, error: message };
    } finally {
      writeState.storySaves.delete(packId);
      ++writeState.registryRevision;
      updateCatalogControls();
    }
  }

  return { showStoriesEmpty, loadStoriesTab, openStoriesFolder, copyStoryPrompt, openStoryDialog, createStory };
}
