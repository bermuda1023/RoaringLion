const PATHS: Record<string, string> = {
  ledger: 'M4 4h16v16H4z M4 9h16 M9 9v11',
  graph: 'M6 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M18 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M12 18m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M7.5 7.5L11 16 M16.5 7.5L13 16',
  index: 'M4 19h16 M6 16V9 M10 16V5 M14 16v-5 M18 16V7',
  list: 'M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01',
  flame: 'M12 3c1 4 5 5 5 10a5 5 0 0 1-10 0c0-3 2-4 2-7 1 1 2 2 3 3 0-2 0-4 0-6z',
  pie: 'M12 3v9h9 M20.5 14.5A9 9 0 1 1 9.5 3.5',
  target: 'M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0 M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0 M12 12h.01',
  branch: 'M6 4v12 M6 16a2 2 0 1 0 0 4a2 2 0 1 0 0-4 M18 6a2 2 0 1 0 0 4a2 2 0 1 0 0-4 M18 10c0 4-6 3-12 6',
  database: 'M12 5c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3z M4 8v8c0 1.7 3.6 3 8 3s8-1.3 8-3V8 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  cpu: 'M7 7h10v10H7z M10 3v4 M14 3v4 M10 17v4 M14 17v4 M3 10h4 M3 14h4 M17 10h4 M17 14h4',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4',
  inbox: 'M4 13h4l2 3h4l2-3h4 M5 5h14l2 8v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6z',
};

export function NavIcon({ name }: { name: string }) {
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={PATHS[name] ?? PATHS.list} />
    </svg>
  );
}
