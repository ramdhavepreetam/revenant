import { useEffect } from 'react';
import { useStore } from '../store';

export function Toast() {
  const { state, dispatch } = useStore();

  useEffect(() => {
    if (!state.toast) return;
    const id = setTimeout(() => dispatch({ type: 'SHOW_TOAST', payload: '' }), 4000);
    return () => clearTimeout(id);
  }, [state.toast, dispatch]);

  if (!state.toast) return null;
  return <div className="toast">{state.toast}</div>;
}
