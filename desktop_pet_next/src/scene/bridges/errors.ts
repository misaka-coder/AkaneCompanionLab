const messages: Record<string, string> = {
  insufficient_coins: "零花钱还不够，先看看口袋里的点心吧。",
  insufficient_inventory: "口袋里还没有这个点心，可以先购买。",
  inventory_empty: "口袋里还没有这个点心，可以先购买。",
  model_response_failed: "她暂时没能回复。已完成的动作会保留，可以只重试回应。",
  model_presentation_invalid: "这次回复的格式不完整，可以重试回应。",
  scene_model_unavailable: "宿主的模型暂不可用，请检查模型连接。",
  presentation_failed: "上次生成未完成，可以重新发起回应。",
  presentation_generating: "这条回复还在生成，请稍后重试取回。",
  model_busy: "宿主正在处理其他回复，请稍后再试。",
  model_generation_failed: "这段剧情暂时未能生成，请重试。",
  model_generation_unavailable: "剧情模型暂不可用，请检查模型连接。",
  model_empty_response: "这次剧情回复没有可播放的内容，请重试。",
  story_cancelled: "已停止这次剧情回复。",
  story_restart_required: "请从故事面板重新进入，载入本次演出的剧本。",
  portrait_needs_transparency: "立绘需要带透明背景，并且包含可见角色。",
  invalid_image_dimensions: "图片边长至少 128 像素，总像素不超过 2400 万。",
  invalid_image: "无法读取这张图片，请选择有效的 PNG、JPEG 或 WebP。",
  resource_unavailable: "这个资源暂不可用，请重新连接刷新资源列表。",
  revision_conflict: "房间状态刚刚更新，请再试一次。",
  delivery_order_invalid: "播放进度同步异常，已自动跳过。",
  presentation_not_found: "未找到当前回复的播放记录。",
  stale_presentation: "回复已更新，旧的播放记录已跳过。",
};
export const friendlyError = (error: unknown) => {
  const text = error instanceof Error ? error.message : String(error);
  return messages[text] || text;
};
