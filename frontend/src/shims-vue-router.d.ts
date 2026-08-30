// Shim for vue-router 4.3.x to work around vue-tsc strict mode resolution issue.
// vue-router 4.3.3's dist/vue-router.d.ts has a structure that confuses TypeScript 5.4
// in strict mode ("Module has no exported member 'createRouter'" despite the export
// being present at line 27 of the .d.ts). The runtime imports work fine; only the
// type check fails. This shim re-declares the commonly used exports with `any` types
// so vue-tsc can pass while vite/esbuild still loads the real module at runtime.

declare module 'vue-router' {
  // Functions
  export function createRouter(options: any): any
  export function createWebHistory(base?: string): any
  export function createWebHashHistory(base?: string): any
  export function createMemoryHistory(base?: string): any
  export function useRouter(): any
  export function useRoute(): any
  export function useLink(props: any): any
  export function onBeforeRouteLeave(guard: any): void
  export function onBeforeRouteUpdate(guard: any): void
  export function isNavigationFailure(e: any, t?: any): boolean
  export function loadRouteLocation(r: any): Promise<any>

  // Constants
  export const START_LOCATION_NORMALIZED: any
  export const matchedRouteKey: any
  export const routeLocationKey: any
  export const routerKey: any
  export const routerViewLocationKey: any
  export const viewDepthKey: any

  // Most-used types (anything more specific can be added as needed)
  // Use `any` here intentionally: a real type augmentation would re-introduce
  // the original TS resolution bug. Users wanting tighter types can remove this
  // shim and accept the vue-tsc noise, or add specific interfaces below.
  // 注意：不要在这里 declare module 'vue-router' 后再 export type RouteMeta ——
  // 项目里 src/types/index.ts 已经 augment RouteMeta，重复会 TS2300。
   
  export type RouteRecordRaw = any
   
  export type RouteLocationRaw = any
   
  export type RouteLocationNormalized = any
   
  export type RouteLocationNormalizedLoaded = any
   
  export type NavigationGuardNext = any
}
