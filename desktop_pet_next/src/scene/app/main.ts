import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import "../styles/tokens.css";
import "../styles/room.css";

createApp(App).use(createPinia()).mount("#scene-app");
