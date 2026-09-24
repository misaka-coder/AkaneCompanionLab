use std::fs;
use serde::{Deserialize, Serialize};

use crate::{
    creator_kit_characters_dir, open_path_in_file_manager, safe_child_path, sanitize_pack_id,
};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct CharacterStorySummary {
    pub file_name: String,
    pub path: String,
    pub story_id: String,
    pub title: String,
    pub description: String,
    pub size_bytes: u64,
    pub modified_ms: u64,
}

#[tauri::command]
pub fn open_character_stories_folder(pack_id: String) -> Result<(), String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }
    let stories_dir = pack_dir.join("stories");
    fs::create_dir_all(&stories_dir).map_err(|error| error.to_string())?;
    open_path_in_file_manager(&stories_dir)
}

#[tauri::command]
pub fn list_character_stories(pack_id: String) -> Result<Vec<CharacterStorySummary>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }
    let stories_dir = pack_dir.join("stories");
    if !stories_dir.is_dir() {
        return Ok(Vec::new());
    }
    let mut items = Vec::new();
    for entry in fs::read_dir(&stories_dir).map_err(|error| error.to_string())? {
        let Ok(entry) = entry else { continue };
        let path = entry.path();
        if !path.is_file() {
            continue;
        };
        let ext = path
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_lowercase();
        if ext != "md" && ext != "json" && ext != "yaml" {
            continue;
        };
        let file_name = path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();
        let stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();
        let metadata = entry.metadata().map_err(|e| e.to_string())?;
        let size_bytes = metadata.len();
        let modified_ms = metadata
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        let mut story_id = stem.clone();
        let mut title = stem.clone();
        let mut description = String::new();

        if ext == "md" {
            if let Ok(content) = fs::read_to_string(&path) {
                if content.starts_with("---") {
                    let parts: Vec<&str> = content.splitn(3, "---").collect();
                    if parts.len() >= 3 {
                        for line in parts[1].lines() {
                            let line = line.trim();
                            if let Some(val) = line.strip_prefix("story_id:") {
                                let v = val.trim().trim_matches(|c| c == '"' || c == '\'');
                                if !v.is_empty() {
                                    story_id = v.to_string();
                                }
                            } else if let Some(val) = line.strip_prefix("title:") {
                                let v = val.trim().trim_matches(|c| c == '"' || c == '\'');
                                if !v.is_empty() {
                                    title = v.to_string();
                                }
                            } else if let Some(val) = line.strip_prefix("description:") {
                                let v = val.trim().trim_matches(|c| c == '"' || c == '\'');
                                if !v.is_empty() {
                                    description = v.to_string();
                                }
                            }
                        }
                    }
                }
            }
        } else if ext == "json" {
            if let Ok(content) = fs::read_to_string(&path) {
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&content) {
                    if let Some(sid) = val.get("story_id").and_then(|s| s.as_str()) {
                        story_id = sid.to_string();
                    }
                    if let Some(t) = val.get("title").and_then(|s| s.as_str()) {
                        title = t.to_string();
                    }
                    if let Some(d) = val.get("description").and_then(|s| s.as_str()) {
                        description = d.to_string();
                    }
                }
            }
        }

        items.push(CharacterStorySummary {
            file_name,
            path: path.to_string_lossy().to_string(),
            story_id,
            title,
            description,
            size_bytes,
            modified_ms,
        });
    }
    items.sort_by(|a, b| a.file_name.cmp(&b.file_name));
    Ok(items)
}

#[tauri::command]
pub fn create_character_story_template(
    pack_id: String,
    story_id: String,
    title: String,
) -> Result<String, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let sid = sanitize_pack_id(&story_id);
    if sid.is_empty() {
        return Err("剧本 ID 不能为空，且仅支持字母、数字与下划线。".to_string());
    }
    let clean_title = title.trim();
    let display_title = if clean_title.is_empty() {
        "新故事"
    } else {
        clean_title
    };

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }
    let stories_dir = pack_dir.join("stories");
    fs::create_dir_all(&stories_dir).map_err(|error| error.to_string())?;
    let file_path = stories_dir.join(format!("{sid}.md"));
    if file_path.exists() {
        return Err(format!("剧本文件 {sid}.md 已存在。"));
    }

    let template = format!(
        r#"---
story_id: {sid}
title: {display_title}
description: 这是一个由角色工坊创建的互动剧本草稿。
cover_image: scenes/家/白天客厅.png
initial_node_id: intro_1
---

## intro_1 [script]
角色: Akane
表情: normal
动作: nod
背景: scenes/家/白天客厅.png
下一幕: choice_branch

你来啦……今天过得怎么样？我刚把屋子收拾整齐呢。

## choice_branch [choice]
标题: 你打算回应……
角色: Akane
表情: 卖萌
动作: idle

有什么有趣的新鲜事想跟我聊聊吗？

? 你的选择:
- “今天遇到了一件很开心的事情！” -> happy_branch
- “稍微有点疲惫，想在你身边安安静静待一会儿。” -> quiet_branch
- （自由对话）直接打字和她说点悄悄话 -> agent_reaction

## happy_branch [script]
角色: Akane
表情: 开心
动作: bounce
下一幕: ending_scene

太好了！看见你这么开心，我也跟着觉得浑身都有干劲了呢！

## quiet_branch [script]
角色: Akane
表情: 脸红
动作: nod
下一幕: ending_scene

快过来歇歇吧……不用勉强多说话，只要你在这里，我就觉得很安心。

## agent_reaction [agent]
角色: Akane
表情: 思考中
动作: nod
目标: 角色正在倾听玩家的日常心声，根据玩家输入的内容温柔好奇地互动，并引导至温馨结局。
下一幕: ending_scene

听你这么说，我好像也更了解你一点了呢……

## ending_scene [ending]
结局标题: 结局：相聚的安心时刻
结局总结: 在静谧而温暖的午后，你与角色的心灵距离悄悄靠近。
表情: 开心
动作: bounce

天色不早啦，但只要你需要，我随时都会在这里等你哦！
"#
    );

    fs::write(&file_path, template).map_err(|e| format!("写入剧本文件失败：{e}"))?;
    Ok(file_path.to_string_lossy().to_string())
}
