import type { Drawer } from "../domain/types";
export const panels: { id: Drawer; name: string; icon: string; label: string; short: string }[] = [
  { id: "story", short: "故事", name: "片刻小剧场", icon: "book", label: "INTERACTIVE STORY" },
  { id: "wardrobe", short: "衣橱", name: "今日衣橱", icon: "clothes", label: "WARDROBE" },
  { id: "pantry", short: "口袋", name: "口袋与点心", icon: "bag", label: "LITTLE TREATS" },
  { id: "places", short: "风景", name: "此刻的风景", icon: "landscape", label: "PLACES & MOMENTS" },
  { id: "music", short: "音乐", name: "房间里的音乐", icon: "music", label: "ROOM RADIO" },
];
