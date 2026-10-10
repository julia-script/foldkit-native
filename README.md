# FoldKit Native

FoldKit apps drawn natively by [GPUI](https://github.com/zed-industries/zed/tree/main/crates/gpui)
(Zed's GPU UI framework), in-process, through [gpuix](https://github.com/remorses/gpuix)'s
Node/Bun bindings. No web view, no browser.

React is to React Native as [FoldKit](https://foldkit.dev) is to FoldKit Native: an
ordinary FoldKit app (its `Runtime`, `h` views, Commands, Subscriptions, CSS
and `@foldkit/ui` components) renders into a native window or a Wayland
layer surface, within the adapter's [capability limits](packages/foldkit-gpuix/CAPABILITIES.md).
FoldKit itself is not modified or forked.

> **Status:** private, under qualification. Runs on Linux (Wayland) and macOS
> (Apple silicon, Metal). gpuix also ships a Windows binary, untested here.

## How it works

FoldKit draws by patching a DOM. The primary renderer, **`foldkit-gpuix`**,
supplies a small DOM API whose nodes are gpuix host nodes. FoldKit's snabbdom
patches update GPUI's retained tree directly, without happy-dom or a mirror:

```text
FoldKit ──snabbdom──▶ native document ──host──▶ gpuix ─▶ GPUI
FoldKit ◀──DOM events── native document ◀──events, focus, scroll── GPUI
```

- **The document and host** (`packages/foldkit-gpuix/src/dom.ts`, `host.ts`)
  provide the DOM APIs FoldKit uses and translate GPUI's input into DOM events.
  Inputs are GPUI editors; the document follows native focus. The host handles
  Tab order, modal focus scopes, pointer events and drag and drop.
- **Styles are CSS.** The adapter's sheet (`packages/foldkit-gpuix/src/sheet.ts`)
  matches supported selectors, resolves custom properties and translates
  styles into what GPUI can draw. GPUI owns layout; geometry reads use its
  last available layout. `:hover`, `:active`, `:focus` and `:focus-visible`,
  live size media queries and viewport units are supported. Sibling selectors,
  pseudo-elements and states on ancestors are among the limits reported in
  `app.unsupported`. Modern CSS such as Tailwind 4's is lowered with
  `bun run css` (`scripts/css.ts`). See the
  [adapter guide](packages/foldkit-gpuix/README.md) and
  [migration guide](packages/foldkit-gpuix/MIGRATING.md) for the supported subset.
  Like a native app, UI text isn't selectable unless its CSS says
  `user-select: text`, so double-clicking a button never highlights its label.
- **Primitives** (`src/primitives.ts`): look-free building blocks for views
  (`box`, `stack`, `row`, `grid`, `text`, `image`, `input`, `scroll`,
  `surface`) with stable `data-part` slots, state attributes and focus/motion
  hooks. No styled components and no theme: those belong to a UI library built
  on top. See [THEMING-SEAMS.md](THEMING-SEAMS.md).
- **Tokens** (`src/theme.ts`): semantic tokens as CSS custom properties,
  switchable at runtime without a restart.

## Use it

In this private checkout, run `npm ci` first, then save this complete counter
as `app.ts` at the repository root and run `bun app.ts`. The repository's
`tsconfig.json` resolves `foldkit-gpuix` to the adapter source; this is not a
published-package installation recipe.

```ts
import { Schema } from 'effect'
import { Runtime } from 'foldkit'
import type { HtmlBuilder } from 'foldkit/html'
import { defineMessageUnion } from 'foldkit/message'
import { mountGpuix } from 'foldkit-gpuix'

const Model = Schema.Struct({ count: Schema.Number })
type Model = typeof Model.Type
const Message = defineMessageUnion({ Incremented: {} })
type Message = typeof Message.Type

const init = () => ({ model: { count: 0 } })
const update = (model: Model, message: Message) =>
  Message.match(message, {
    Incremented: () => ({ model: { count: model.count + 1 } }),
  })
const view = (model: Model, h: HtmlBuilder<Message>) =>
  h.div([h.Class('app')], [
    h.p([], [`Count: ${model.count}`]),
    h.button([h.OnClick(Message.Incremented())], ['+1']),
  ])
const css = '.app { padding: 24px; gap: 12px; display: flex; flex-direction: column; }'

const app = mountGpuix({ title: 'My app', width: 800, height: 600, css })
app.own(Runtime.embed(Runtime.makeElement({ Model, init, update, view, container: app.container })))
```

`own()` ties the runtime's disposal handle to the app. `app.close()` disposes
owned runtimes before releasing the adapter's tree, listeners, frames and
globals. By default closing ends the process. With `exitOnClose: false`, a
host can await `app.close()` and `app.closed`, but gpuix 0.10 cannot release
its native window or event callback from JavaScript. On macOS the red close
button exits inside GPUI without running JavaScript close handlers. See
[closing, errors and starting](packages/foldkit-gpuix/README.md#closing-errors-and-starting)
for those limits. `Runtime.run` provides no disposal handle.

`mountGpuix` accepts gpuix's window options, including `layerShell` for a
Wayland bar, dock or overlay. `appId` selects a durable `localStorage` data
folder; without it or `dataDir`, storage is in memory. Pass `tokens` and use
`app.setTokens(otherTokens)` to switch CSS custom properties live.

The [setup test](test/readme-setup.test.ts) typechecks this exact snippet and
executes its model, view and owned runtime on the real adapter with a fake
renderer. Native startup of this snippet remains pending platform qualification;
headless checks do not establish window or pixel behavior.

### Mirror comparator and fallback

The older happy-dom → MutationObserver → GPUI mirror (`src/mirror.ts`) stays
available for comparison and fallback while the adapter is qualified. Its
CSS cascade and browser emulation have different limits. Existing mirror apps
can still use `mountNative` from `foldkit-native`; use the direct adapter
above for new setup. To compare a ported example:

```sh
FOLDKIT_NATIVE_RENDERER=mirror bun run example weather
```

Examples default to the adapter. Native UI and Layer Bar require it; see
[EXAMPLES.md](EXAMPLES.md) for renderer selection.

## Run the examples

Needs [Bun](https://bun.sh) and Node's `npm` (for the exact lockfile). On
Linux, also Wayland, EGL/Vulkan and libxkbcommon on the library path (NixOS:
put them on `LD_LIBRARY_PATH`, or use nixos-config's `nix develop .#render`).
Without them gpuix says `Cannot find native binding. npm has a bug…`, which
means a missing system library, not a missing package. On macOS nothing else: gpuix's Apple silicon
binary draws with Metal. Run it from a terminal in the logged-in desktop
session (Terminal.app, at the machine or over Screen Sharing) so the window
appears on that screen.

```sh
npm ci                        # exact pins; installs only your platform's gpuix binary
bun run gallery               # every example in one window; click one to open it
bun run gallery --list        # the same list in the terminal
bun run example weather       # open one example directly
bun run demo                  # examples/themes.ts: primitives + two token sets, switched at runtime
bun run counter               # examples/counter.ts: a FoldKit counter; only the setup import is native-specific
bun test                      # everything this machine can run (TESTING.md)
bun run typecheck
bun run css                   # regenerate the examples' CSS after changing classes or styles.css
bun run record weather        # macOS: a still and a clip of an example, from its own frames (needs ffmpeg)
```

The examples are FoldKit's own example apps (weather, kanban, snake…), ported
with their tests, plus showcases written here: [EXAMPLES.md](EXAMPLES.md).

In the demo, click a row to select it and **switch theme** to swap the whole
token set live; `THEME=paper bun run demo` starts on the light set.

`bun build --compile app.ts` makes one executable. Ship gpuix's `.node` file
next to it and set `NAPI_RS_NATIVE_LIBRARY_PATH` to it: gpuix's loader picks its
platform package dynamically, so the compiler can't embed it on its own.

**Pin gpuix exactly** (`@gpuix/native` and its platform package, same version),
as gpuix's README insists.

## Measured (mirror; jemera presentations screen, Umbriel VM, virgl/OpenGL)

| | |
|---|---|
| Process start → first frame | ~480 ms |
| Memory | ~186 MB, one process |
| Click → updated frame | 7–34 ms (style resolution dominates) |
| 60 Hz animation | 59.75 fps; change → frame median 2.2 ms, 95th 6.4 ms |

## What it would take to be a real package

1. **Ship it:** build to JS for Node as well as Bun, publish emitted types
   (including window and `layerShell` options), and a `--compile` recipe that
   embeds the `.node` file. These are future packaging work; the checkout
   currently exports TypeScript source.
2. **Layout and overlays.** The adapter already reads GPUI's last layout,
   exposes `ResizeObserver` and scroll positions, and supports `anchored`
   popovers and `virtual-list`. Fresh layout still depends on painting;
   geometry queries can block when GPUI stops answering. Virtual lists have
   their own layout limits. See the adapter guide's
   [geometry](packages/foldkit-gpuix/README.md#geometry) and
   [native elements](packages/foldkit-gpuix/README.md#gpuis-own-elements-anchored-and-virtual-list).
3. **Inline text.** GPUI has blocks and flex, not inline formatting; mixed runs
   (`<p>a <b>b</b> c</p>`) become a wrapping row today.
4. **Text editing.** Native inputs, controlled values, focus synchronization,
   keyboard navigation and modal focus scopes are implemented. IME composition
   and `selectionchange` events remain gaps, and password fields are refused
   because gpuix has no masked input.
5. **CSS coverage and speed.** The direct adapter avoids happy-dom's
   `getComputedStyle` path and supports live viewport units and auto-margin
   centering. Remaining limits include sibling/structural selectors,
   pseudo-elements, ancestor state selectors, rotation/scaling transforms,
   and native CSS transitions and keyframes. Keep qualification and performance budgets separate from
   retained-tree checks; see [CAPABILITIES.md](packages/foldkit-gpuix/CAPABILITIES.md).
6. **Mirror maintenance.** happy-dom's cached descendant selector matches
   are not invalidated when an ancestor's attribute changes; the mirror clears
   caches when it restyles. This comparator limitation is separate from the
   direct adapter's sheet.
7. **Accessibility.** Roles and accessible names already reach AccessKit.
   Checked state is value text and disabled state is missing; real screen
   reader qualification remains future work.
8. **More tests.** The CI workflow runs the adapter, components and mirror
   comparator headless, and real-GPUI tests on macOS.
   [TESTING.md](TESTING.md) lists coverage and future layers (browser
   conformance, performance budgets, more platforms). Linux headless results
   do not qualify native Wayland behavior.
9. **Theming seams** listed in [THEMING-SEAMS.md](THEMING-SEAMS.md) (per-element
   blur, multi-stop gradients, colour transitions…).

## Credits and licences

FoldKit Native is MIT licensed ([LICENSE](LICENSE)). Its examples include
FoldKit's example apps, MIT licensed, © 2025 Devin Jameson
([examples/FOLDKIT-LICENSE](examples/FOLDKIT-LICENSE)); each ported file says
so. Otherwise it depends on, and does not copy code from:

| Project | Licence | Used for |
|---|---|---|
| [FoldKit and @foldkit/ui](https://github.com/foldkit/foldkit) | MIT | The framework this renders (peer dependency) |
| [gpuix](https://github.com/remorses/gpuix) (`@gpuix/native`, incl. its GPUI build) | Apache-2.0 | GPUI bindings and the native renderer |
| [happy-dom](https://github.com/capricorn86/happy-dom) | MIT | The mirror comparator's DOM |
| [Effect](https://effect.website) | MIT | FoldKit's runtime (peer dependency) |
| [Tailwind CSS](https://tailwindcss.com) and [Lightning CSS](https://lightningcss.dev) | MIT, MPL-2.0 | Building the examples' CSS (dev only) |
| [clsx](https://github.com/lukeed/clsx), [fractional-indexing](https://github.com/rocicorp/fractional-indexing) | MIT, CC0-1.0 | Used by FoldKit's example apps (dev only) |

All are compatible with MIT. If you distribute a compiled binary that embeds
gpuix's native library, include its Apache-2.0 licence and notices with it.
