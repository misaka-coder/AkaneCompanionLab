import { escapeHtml, initial, safeStyleUrl } from "../dom.js";
import { actionPhase, renderActionButton } from "./action-button.js";
import { renderPresentationControls } from "./presentation-controls.js";

export function renderCharacterAppearance(state) {
  const vm = state.viewModel;
  if (!vm?.character) {
    return `<section class="empty-state glass-panel"><span aria-hidden="true">◇</span><h2>还没有可展示的角色</h2><p>连接桌宠或在角色工坊创建角色包后，这里会显示真实资源。</p>${vm ? renderActionButton(state, vm, "character.openWorkshop", "＋", "打开角色工坊", "primary") : ""}</section>`;
  }

  const character = vm.character;
  const portraitStyle = safeStyleUrl(character.visuals.portrait);
  const backgroundStyle = safeStyleUrl(character.visuals.hero);
  const selectedPack = character.availablePacks.find((item) => item.selected || item.id === character.packId);

  return `
    <section class="ccv2-appearance" aria-labelledby="ccv2-page-title">
      <div class="appearance-heading">
        <div><p class="eyebrow">CHARACTER & APPEARANCE</p><h2>她现在的样子</h2><p>快速切换真实角色资源；完整人设、上传与构图校准仍由角色工坊负责。</p></div>
        <div class="heading-actions">
          ${renderActionButton(state, vm, "character.openPackFolder", "⌁", "角色文件夹")}
          ${renderActionButton(state, vm, "character.openWorkshop", "◇", "打开角色工坊", "primary")}
        </div>
      </div>

      <div class="appearance-layout">
        <section class="character-preview glass-panel">
          ${backgroundStyle ? `<div class="appearance-background" style="--appearance-background:${backgroundStyle}"></div>` : ""}
          <div class="preview-wash"></div>
          <div class="preview-copy">
            <span class="preview-status"><i></i>${escapeHtml(vm.shell.connected ? "实时资源" : "离线预览")}</span>
            <p class="eyebrow">ACTIVE CHARACTER</p>
            <h3>${escapeHtml(character.displayName)}</h3>
            <p>${escapeHtml([character.outfit, character.emotion].filter(Boolean).join(" · ") || selectedPack?.description || "等待角色资源")}</p>
            <div class="preview-resource-counts">
              <span><strong>${character.outfits.length}</strong><small>服装</small></span>
              <span><strong>${character.emotions.length}</strong><small>表情</small></span>
              <span><strong>${character.availablePacks.length}</strong><small>角色包</small></span>
            </div>
          </div>
          <div class="appearance-portrait-stage">
            <span class="portrait-halo"></span>
            ${portraitStyle
              ? `<div class="appearance-portrait" role="img" aria-label="${escapeHtml(character.displayName)} 当前立绘" style="--portrait-image:${portraitStyle}"></div>`
              : `<div class="appearance-portrait-fallback" aria-label="当前角色暂无立绘">${initial(character.displayName)}</div>`}
          </div>
        </section>

        <div class="appearance-settings">
          ${renderPresentationControls(state, character)}
          ${renderPackPicker(state, vm, character)}
          ${renderOutfitPicker(state, vm, character)}
          ${renderEmotionPicker(state, vm, character)}
          ${renderResourcePanel(state, vm, character)}
        </div>
      </div>
    </section>`;
}

function renderPackPicker(state, vm, character) {
  const packs = character.availablePacks;
  return `
    <section class="appearance-card glass-panel">
      <div class="appearance-card-head"><div><p class="eyebrow">CHARACTER PACK</p><h3>当前角色</h3></div><span class="mini-chip">${packs.length ? `${packs.length} 个可用` : "当前角色"}</span></div>
      ${packs.length ? `<div class="character-pack-list">${packs.map((pack) => {
        const selected = pack.selected || pack.id === character.packId;
        const pending = actionPhase(state, "character.selectPack") === "pending" || actionPhase(state, "character.selectPack") === "pressed";
        return `<button class="character-pack-option${selected ? " is-selected" : ""}" type="button" data-action="character.selectPack" data-action-value="${escapeHtml(pack.id)}" ${selected || pending || !vm.actions["character.selectPack"]?.available ? "disabled" : ""}><span class="pack-initial">${initial(pack.displayName)}</span><span><strong>${escapeHtml(pack.displayName)}</strong><small>${escapeHtml(pack.description || pack.id)}</small></span><i>${selected ? "当前" : "切换"}</i></button>`;
      }).join("")}</div>` : `<div class="empty-inline"><span>只读取到当前角色</span><small>可在角色工坊创建或导入更多角色包</small></div>`}
    </section>`;
}

function renderOutfitPicker(state, vm, character) {
  const items = character.outfits;
  return `
    <section class="appearance-card glass-panel">
      <div class="appearance-card-head"><div><p class="eyebrow">OUTFIT</p><h3>服装</h3></div><span class="mini-chip">${items.length ? `${items.length} 套` : "无资源"}</span></div>
      ${items.length ? `<div class="visual-choice-grid">${items.map((item) => renderVisualChoice(state, vm, "character.setOutfit", item, "衣")).join("")}</div>` : `<div class="empty-inline"><span>当前角色没有可切换服装</span><small>在角色工坊添加服装资源后会出现在这里</small></div>`}
    </section>`;
}

function renderEmotionPicker(state, vm, character) {
  const items = character.emotions;
  return `
    <section class="appearance-card glass-panel">
      <div class="appearance-card-head"><div><p class="eyebrow">EXPRESSION</p><h3>表情预览</h3></div><span class="mini-chip">${items.length ? `${items.length} 个` : "无资源"}</span></div>
      ${items.length ? `<div class="visual-choice-grid is-emotion-grid">${items.map((item) => renderVisualChoice(state, vm, "character.previewEmotion", item, "颜")).join("")}</div>` : `<div class="empty-inline"><span>当前服装没有表情资源</span><small>切换服装或进入角色工坊检查资源</small></div>`}
    </section>`;
}

function renderVisualChoice(state, vm, actionId, item, fallback) {
  const imageStyle = safeStyleUrl(item.image);
  const pending = ["pressed", "pending"].includes(actionPhase(state, actionId));
  const available = vm.actions[actionId]?.available;
  return `<button class="visual-choice${item.current ? " is-selected" : ""}" type="button" data-action="${escapeHtml(actionId)}" data-action-value="${escapeHtml(item.id)}" title="${escapeHtml(item.current ? `${item.name}（当前）` : item.name)}" ${item.current || pending || !available ? "disabled" : ""}>${imageStyle ? `<span class="visual-thumb" style="--choice-image:${imageStyle}"></span>` : `<span class="visual-thumb visual-thumb-fallback">${fallback}</span>`}<span><strong>${escapeHtml(item.name)}</strong><small>${item.current ? "当前" : pending ? "切换中" : "点击预览"}</small></span></button>`;
}

function renderResourcePanel(state, vm, character) {
  return `
    <section class="appearance-card resource-health glass-panel">
      <div class="appearance-card-head"><div><p class="eyebrow">RESOURCE HEALTH</p><h3>资源状态</h3></div>${renderActionButton(state, vm, "character.refresh", "↻", "重新检查")}</div>
      ${character.resourceWarnings.length
        ? `<ul class="appearance-warning-list">${character.resourceWarnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
        : `<div class="resource-ready"><i>✓</i><span><strong>当前资源可用</strong><small>桌宠与控制中心正在使用同一角色资源。</small></span></div>`}
    </section>`;
}
