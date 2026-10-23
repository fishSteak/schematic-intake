import { computed, reactive } from 'vue';
import { api } from './api';
export const workspace = reactive({ boards: [], runtime: null, boardId: '', loading: true, error: '', initialized: false });
export const currentBoard = computed(() => workspace.boards.find(board => String(board.id) === workspace.boardId));
let pending;
export function loadWorkspace() {
  if (pending) return pending;
  workspace.loading = true;
  pending = (async () => {
    try {
      const [boards, runtime] = await Promise.all([api.boards(), api.runtime()]);
      workspace.boards = boards.boards || []; workspace.runtime = runtime;
      if (!workspace.boards.some(b => String(b.id) === workspace.boardId)) {
        let saved = ''; try { saved = localStorage.getItem('schematic-board') || ''; } catch { /* optional storage */ }
        workspace.boardId = String(workspace.boards.find(b => String(b.id) === saved)?.id || workspace.boards[0]?.id || '');
      }
      workspace.error = ''; workspace.initialized = true;
    } catch (error) { workspace.error = error.message; }
    finally { workspace.loading = false; pending = null; }
  })();
  return pending;
}
export function selectBoard(id) { workspace.boardId = String(id || ''); try { localStorage.setItem('schematic-board', workspace.boardId); } catch { /* optional storage */ } }
