import { createContext, Fragment, useCallback, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { AlertCircle, Eye, EyeOff, LockKeyhole, LogIn, LogOut, RefreshCw, UserRound } from 'lucide-react';
import { AUTH_EXPIRED_EVENT, setRequestAccess } from './data';
import './auth.css';

export type AuthUser = { username: string; role: 'analyst' | 'viewer' };
export type Access = { enabled: boolean; user: AuthUser | null; canAnalyze: boolean; logout: () => Promise<void>; csrfToken?: string | null };
export const AuthContext = createContext<Access>({ enabled: false, user: null, canAnalyze: true, logout: async () => {}, csrfToken: null });
export const useAccess = () => useContext(AuthContext);
export function AnalystOnly({ children }: { children: ReactNode }) { return useAccess().canAnalyze ? <>{children}</> : null; }
export const analystRoute = (path: string) => ['/updates', '/tdm', '/service/constant-prices'].includes(path.replace(/\/+$/, '') || '/');

type Session = { enabled: boolean; user: AuthUser | null; csrfToken: string | null; secureCookie?: boolean };
type State = { phase: 'checking' | 'ready' | 'error' | 'login' | 'logout' | 'logout-error'; session: Session; error: string; version: number };
const emptySession: Session = { enabled: true, user: null, csrfToken: null };
const initialState: State = { phase: 'checking', session: emptySession, error: '', version: 0 };
const identity = (session: Session) => JSON.stringify([session.enabled, session.user?.username, session.user?.role]);

export function readAuthSession(value: unknown): Session {
  if (!value || typeof value !== 'object') throw new Error('Некорректный ответ сервера авторизации.');
  const row = value as Record<string, unknown>, user = row.user as Record<string, unknown> | null;
  if (typeof row.enabled !== 'boolean' || !(row.csrfToken === null || typeof row.csrfToken === 'string') || !(row.secureCookie === undefined || typeof row.secureCookie === 'boolean') || !(user === null || user && typeof user === 'object' && typeof user.username === 'string' && user.username.trim() && (user.role === 'analyst' || user.role === 'viewer'))) throw new Error('Некорректный ответ сервера авторизации.');
  return { enabled: row.enabled, user: user === null ? null : { username: user.username as string, role: user.role as AuthUser['role'] }, csrfToken: row.csrfToken as string | null, ...(row.secureCookie === undefined ? {} : { secureCookie: row.secureCookie as boolean }) };
}
async function authRequest(path: string, signal: AbortSignal, init: RequestInit = {}) {
  signal.throwIfAborted();
  const controller = new AbortController(), cancel = () => controller.abort(signal.reason);
  signal.addEventListener('abort', cancel, { once: true });
  const timer = window.setTimeout(() => controller.abort(new DOMException('Сервер не ответил вовремя. Повторите попытку.', 'TimeoutError')), 15000);
  try {
    const response = await fetch(`/api/v1/auth/${path}`, { ...init, credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
    const value: unknown = response.ok && path !== 'logout' ? await response.json() : null;
    return { ok: response.ok, status: response.status, value };
  }
  finally { window.clearTimeout(timer); signal.removeEventListener('abort', cancel); }
}
async function sessionRequest(signal: AbortSignal): Promise<Session> {
  const response = await authRequest('session', signal);
  if (response.status === 401) return emptySession;
  if (!response.ok) throw new Error('Не удалось проверить сессию. Повторите попытку.');
  return readAuthSession(response.value);
}
type Actions = { state: State; login: (username: string, password: string) => Promise<boolean>; retry: () => void };
const ActionsContext = createContext<Actions | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(initialState), current = useRef(state), active = useRef<AbortController>(), alive = useRef(false);
  const generation = useRef(0), denied = useRef(new Set<string>()), logoutToken = useRef<string | null>(null), refreshLogout = useRef(false);
  current.current = state;
  const publish = useCallback((next: State) => { current.current = next; setState(next); }, []);
  const refresh = useCallback(async (blocking = false) => {
    if (!blocking && (active.current || current.current.phase !== 'ready')) return;
    active.current?.abort();
    const controller = new AbortController(), sequence = ++generation.current, before = current.current;
    active.current = controller;
    if (blocking) publish({ ...before, phase: 'checking', session: emptySession, error: '' });
    try {
      const session = await sessionRequest(controller.signal);
      if (!alive.current || controller.signal.aborted || sequence !== generation.current) return;
      const changed = identity(before.session) !== identity(session);
      if (changed) denied.current.clear();
      publish({ phase: 'ready', session, error: '', version: before.version + Number(changed || blocking) });
    } catch {
      if (alive.current && !controller.signal.aborted && sequence === generation.current) publish({ phase: 'error', session: emptySession, error: 'Не удалось проверить сессию. Проверьте соединение и повторите попытку.', version: before.version + 1 });
    } finally { if (active.current === controller) active.current = undefined; }
  }, [publish]);
  useEffect(() => {
    alive.current = true;
    const restore = setRequestAccess(() => current.current.session);
    void refresh(true);
    const expired = (event: Event) => {
      const state = current.current;
      if (state.phase !== 'ready' || !state.session.enabled || !state.session.user) return;
      const detail = (event as CustomEvent<{ url?: string; status?: number }>).detail;
      const key = JSON.stringify([detail?.url, detail?.status, identity(state.session)]);
      if (denied.current.has(key)) return;
      denied.current.add(key);
      flushSync(() => { void refresh(true); });
    };
    const focus = () => { if (document.visibilityState !== 'hidden') void refresh(); };
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);
    window.addEventListener('focus', focus);
    const timer = window.setInterval(() => { void refresh(); }, 60000);
    return () => { alive.current = false; generation.current++; active.current?.abort(); active.current = undefined; window.clearInterval(timer); window.removeEventListener(AUTH_EXPIRED_EVENT, expired); window.removeEventListener('focus', focus); restore(); };
  }, [refresh]);
  const login = useCallback(async (username: string, password: string) => {
    if (!alive.current || current.current.phase !== 'ready' || current.current.session.user) return false;
    if (current.current.session.enabled && current.current.session.secureCookie && window.location.protocol !== 'https:') return false;
    active.current?.abort();
    const controller = new AbortController(), sequence = ++generation.current, before = current.current;
    active.current = controller;
    publish({ ...before, phase: 'login', error: '' });
    try {
      const response = await authRequest('login', controller.signal, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
      if (!response.ok) throw new Error(response.status === 401 ? 'Неверное имя пользователя или пароль.' : response.status === 426 ? 'Для входа необходимо защищённое подключение HTTPS.' : response.status === 429 ? 'Слишком много попыток входа. Повторите позже.' : 'Не удалось войти. Повторите попытку.');
      const session = readAuthSession(response.value);
      if (session.enabled && !session.user) throw new Error('Вход не подтверждён сервером.');
      if (!alive.current || controller.signal.aborted || sequence !== generation.current) return false;
      denied.current.clear();
      publish({ phase: 'ready', session, error: '', version: before.version + 1 });
      return true;
    } catch (error) {
      if (alive.current && !controller.signal.aborted && sequence === generation.current) publish({ ...before, phase: 'ready', session: emptySession, error: error instanceof SyntaxError ? 'Сервер вернул некорректный ответ. Повторите попытку.' : error instanceof Error && !(error instanceof TypeError) ? error.message : 'Не удалось подключиться к серверу. Повторите попытку.' });
      return false;
    } finally { if (active.current === controller) active.current = undefined; }
  }, [publish]);
  const logout = useCallback(async () => {
    if (!alive.current || current.current.phase === 'logout') return;
    const before = current.current;
    if (!before.session.enabled) return;
    logoutToken.current ??= before.session.csrfToken;
    active.current?.abort();
    const controller = new AbortController(), sequence = ++generation.current;
    active.current = controller;
    flushSync(() => publish({ phase: 'logout', session: emptySession, error: '', version: before.version + 1 }));
    try {
      let needsLogout = true;
      if (!logoutToken.current || refreshLogout.current) {
        const session = await sessionRequest(controller.signal);
        needsLogout = session.enabled && !!session.user;
        logoutToken.current = session.csrfToken;
      }
      if (needsLogout) {
        if (!logoutToken.current) throw new Error('Не удалось подтвердить выход. Повторите попытку.');
        const response = await authRequest('logout', controller.signal, { method: 'POST', headers: { 'X-CSRF-Token': logoutToken.current } });
        if (!response.ok && response.status !== 401) { refreshLogout.current = response.status === 403; throw new Error('Не удалось завершить выход на сервере. Повторите попытку.'); }
      }
      if (!alive.current || controller.signal.aborted || sequence !== generation.current) return;
      logoutToken.current = null; refreshLogout.current = false; denied.current.clear();
      publish({ phase: 'ready', session: emptySession, error: '', version: before.version + 1 });
    } catch {
      if (alive.current && !controller.signal.aborted && sequence === generation.current) publish({ phase: 'logout-error', session: emptySession, error: 'Не удалось завершить выход на сервере. Повторите попытку.', version: before.version + 1 });
    } finally { if (active.current === controller) active.current = undefined; }
  }, [publish]);
  const access: Access = { enabled: state.session.enabled, user: state.session.user, canAnalyze: state.phase === 'ready' && (!state.session.enabled || state.session.user?.role === 'analyst'), logout, csrfToken: state.session.csrfToken };
  return <AuthContext.Provider value={access}><ActionsContext.Provider value={{ state, login, retry: () => { void refresh(true); } }}>{children}</ActionsContext.Provider></AuthContext.Provider>;
}

function AuthShell({ children }: { children: ReactNode }) {
  return <main className="auth-page"><header className="auth-brand"><img src="/brand-moscow.svg" alt="" /><div><strong>Аналитика Москвы</strong><span>Недвижимость и экономика</span></div></header><div className="auth-form">{children}</div></main>;
}
function LoginForm({ state, login }: Pick<Actions, 'state' | 'login'>) {
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [visible, setVisible] = useState(false);
  const passwordInput = useRef<HTMLInputElement>(null), busy = state.phase === 'login';
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !username.trim() || !password) return;
    const success = await login(username.trim(), password);
    setPassword('');
    if (!success) passwordInput.current?.focus();
  }
  return <AuthShell><form onSubmit={event => { void submit(event); }} aria-labelledby="auth-title" aria-busy={busy}>
    <h1 id="auth-title">Вход</h1>
    <label htmlFor="auth-username">Имя пользователя</label><input id="auth-username" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required autoFocus disabled={busy} value={username} onChange={event => setUsername(event.target.value)} />
    <label htmlFor="auth-password">Пароль</label><div className="auth-password"><input ref={passwordInput} id="auth-password" name="password" type={visible ? 'text' : 'password'} autoComplete="current-password" required disabled={busy} aria-invalid={!!state.error} aria-describedby={state.error ? 'auth-error' : undefined} value={password} onChange={event => setPassword(event.target.value)} /><button type="button" className="icon-button" disabled={busy} title={visible ? 'Скрыть пароль' : 'Показать пароль'} aria-label={visible ? 'Скрыть пароль' : 'Показать пароль'} aria-pressed={visible} onClick={() => setVisible(value => !value)}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button></div>
    {state.error && <p id="auth-error" className="auth-error" role="alert"><AlertCircle size={17} />{state.error}</p>}
    <button type="submit" className="auth-submit" disabled={busy || !username.trim() || !password}>{busy ? <RefreshCw size={18} className="loading-icon" /> : <LogIn size={18} />}{busy ? 'Вход…' : 'Войти'}</button>
  </form></AuthShell>;
}
export function AuthGate({ children }: { children: ReactNode }) {
  const actions = useContext(ActionsContext), access = useAccess();
  if (!actions) return <>{children}</>;
  const { state } = actions;
  if (state.phase === 'checking' || state.phase === 'logout') return <AuthShell><div className="auth-state" role="status"><RefreshCw className="loading-icon" size={22} /><p>{state.phase === 'logout' ? 'Выход…' : 'Проверка сессии…'}</p></div></AuthShell>;
  if (state.phase === 'error' || state.phase === 'logout-error') return <AuthShell><div className="auth-state"><p className="auth-error" role="alert"><AlertCircle size={18} />{state.error}</p><button type="button" className="auth-retry" onClick={state.phase === 'logout-error' ? () => { void access.logout(); } : actions.retry}><RefreshCw size={17} />{state.phase === 'logout-error' ? 'Повторить выход' : 'Повторить проверку'}</button></div></AuthShell>;
  if (state.session.enabled && state.session.secureCookie && typeof window !== 'undefined' && window.location.protocol !== 'https:') {
    const url = new URL(window.location.href); url.protocol = 'https:';
    return <AuthShell><div className="auth-state"><h1>Защищённое подключение</h1><p className="auth-error" role="alert"><LockKeyhole size={18} />Для входа необходимо защищённое подключение HTTPS.</p><a className="auth-retry" href={url.href}><LockKeyhole size={17} />Открыть HTTPS</a><button type="button" className="auth-retry" onClick={actions.retry}><RefreshCw size={17} />Повторить проверку</button></div></AuthShell>;
  }
  if (state.session.enabled && !state.session.user) return <LoginForm state={state} login={actions.login} />;
  return <Fragment key={`${state.version}:${identity(state.session)}`}>{children}</Fragment>;
}
export function AuthAccount() {
  const { enabled, user, logout } = useAccess();
  if (!enabled || !user) return null;
  return <div className="auth-account" aria-label="Учётная запись"><UserRound size={17} /><div><strong>{user.username}</strong><span>{user.role === 'analyst' ? 'Аналитик' : 'Просмотр'}</span></div><button type="button" className="icon-button" title="Выйти" aria-label="Выйти" onClick={() => { void logout(); }}><LogOut size={18} /></button></div>;
}
