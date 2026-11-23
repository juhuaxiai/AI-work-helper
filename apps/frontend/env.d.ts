/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly PUBLIC_PROJECT_API_BASE_URL: string;
}

declare module "*.vue" {
  import type { DefineComponent } from "vue";

  const component: DefineComponent<object, object, unknown>;
  export default component;
}
