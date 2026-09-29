import { BriefcaseBusiness, FileText, LogOut, Network, Radio } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import type { User } from '../../lib/api';

function Brand() {
  return <NavLink to="/" className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-2xl bg-orange text-lg font-black text-white shadow-[0_5px_0_#C94F12]">N</div><span className="text-xl font-black tracking-tight text-ink">Niko<span className="text-orange">Seek</span></span></NavLink>;
}

export function AppNavbar({ user, onLogout }: { user: User; onLogout: () => void }) {
  const linkClass = ({ isActive }: { isActive: boolean }) => `flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-bold transition ${isActive ? 'bg-[#FFE1C0] text-orange' : 'text-[#805940] hover:bg-[#FFF0DE] hover:text-orange'}`;
  return <header className="sticky top-0 z-20 border-b border-[#EFD3B0]/80 bg-[#FFF7EB]/90 px-5 py-3 backdrop-blur sm:px-9"><div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3"><Brand /><nav className="order-3 flex w-full gap-1 sm:order-none sm:w-auto"><NavLink end to="/" className={linkClass}><BriefcaseBusiness size={17} />Jobs</NavLink><NavLink end to="/workflow" className={linkClass}><Network size={17} />Config workflow</NavLink><NavLink to="/workflow/live" className={linkClass}><Radio size={17} />Live workflow</NavLink><NavLink to="/profile" className={linkClass}><FileText size={17} />Profile</NavLink></nav><div className="flex items-center gap-2"><div className="hidden text-right sm:block"><p className="text-xs font-bold text-[#9A6B4D]">Signed in as</p><p className="text-sm font-black">@{user.username}</p></div><div className="grid h-9 w-9 place-items-center rounded-xl bg-[#FFDFC0] text-sm font-black text-orange">{user.username.slice(0, 1).toUpperCase()}</div><button onClick={onLogout} className="flex items-center gap-2 rounded-xl px-2 py-2 text-sm font-bold text-[#87512F] hover:bg-[#FFE7CB] hover:text-orange" aria-label="Sign out"><LogOut size={17} /><span className="hidden sm:inline">Sign out</span></button></div></div></header>;
}
