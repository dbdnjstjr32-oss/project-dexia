// Drone Garage — CRUD modal for custom airframe physics profiles.
// Pure React state (no Redux/zustand). Talks to /api/profiles which persists
// to the shared drone_profiles.json consumed by the Python MuJoCo builder.

import { useEffect, useState } from 'react';

const TOPOLOGIES = [
  { id: 'quad', label: 'Quad (4)' },
  { id: 'hexa', label: 'Hexa (6)' },
  { id: 'tandem', label: 'Tandem VTOL (4)' },
];

const BLANK = {
  id: null,
  name: 'New Airframe',
  topology: 'quad',
  mass: 0.8,
  arm_length: 0.13,
  max_thrust: 8.0,
  drag_coeff: 0.15,
};

const NUMERIC = {
  mass: { label: 'Mass (kg)', step: 0.05, min: 0.05 },
  arm_length: { label: 'Arm Length (m)', step: 0.01, min: 0.02 },
  max_thrust: { label: 'Max Motor Thrust (N)', step: 0.5, min: 0.1 },
  drag_coeff: { label: 'Drag Coefficient', step: 0.05, min: 0 },
};

export default function DroneGarage({ open, onClose }) {
  const [profiles, setProfiles] = useState([]);
  const [form, setForm] = useState(BLANK);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  async function load() {
    try {
      const r = await fetch('/api/profiles');
      const j = await r.json();
      setProfiles(j.profiles || []);
    } catch (e) {
      setMsg('Failed to load profiles: ' + e);
    }
  }

  useEffect(() => {
    if (open) load();
  }, [open]);

  if (!open) return null;

  const isEditing = !!form.id;

  function selectProfile(p) {
    setForm({ ...p });
    setMsg(null);
  }
  function newProfile() {
    setForm({ ...BLANK });
    setMsg(null);
  }
  function setField(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const method = isEditing ? 'PUT' : 'POST';
      const r = await fetch('/api/profiles', {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const j = await r.json();
      if (j.profile) {
        setForm({ ...j.profile });
        setMsg(`Saved “${j.profile.name}”.`);
        await load();
      } else {
        setMsg('Save failed: ' + (j.error || 'unknown'));
      }
    } catch (e) {
      setMsg('Save error: ' + e);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!form.id) return;
    setBusy(true);
    try {
      await fetch(`/api/profiles?id=${encodeURIComponent(form.id)}`, { method: 'DELETE' });
      setMsg('Deleted.');
      newProfile();
      await load();
    } catch (e) {
      setMsg('Delete error: ' + e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={S.overlay} onClick={onClose}>
      <div style={S.modal} onClick={(e) => e.stopPropagation()}>
        <div style={S.header}>
          <span style={S.title}>◈ DRONE GARAGE</span>
          <span style={S.sub}>custom airframe physics profiles</span>
          <button style={S.close} onClick={onClose}>✕</button>
        </div>

        <div style={S.body}>
          {/* Left: profile list */}
          <div style={S.list}>
            <button style={S.newBtn} onClick={newProfile}>+ New Airframe</button>
            {profiles.map((p) => (
              <div
                key={p.id}
                onClick={() => selectProfile(p)}
                style={{ ...S.listItem, ...(form.id === p.id ? S.listItemActive : {}) }}
              >
                <div style={S.listName}>{p.name}</div>
                <div style={S.listMeta}>
                  {p.topology} · {p.mass}kg · arm {p.arm_length}m
                </div>
              </div>
            ))}
            {profiles.length === 0 && <div style={S.dim}>No profiles yet.</div>}
          </div>

          {/* Right: editor form */}
          <div style={S.formCol}>
            <label style={S.lbl}>Profile Name</label>
            <input
              style={S.input}
              value={form.name}
              onChange={(e) => setField('name', e.target.value)}
            />

            <label style={S.lbl}>Topology Type</label>
            <div style={S.topoRow}>
              {TOPOLOGIES.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setField('topology', t.id)}
                  style={{
                    ...S.topoBtn,
                    ...(form.topology === t.id ? S.topoBtnActive : {}),
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>

            <div style={S.grid2}>
              {Object.entries(NUMERIC).map(([key, cfg]) => (
                <div key={key}>
                  <label style={S.lbl}>{cfg.label}</label>
                  <input
                    style={S.input}
                    type="number"
                    step={cfg.step}
                    min={cfg.min}
                    value={form[key]}
                    onChange={(e) => setField(key, e.target.value)}
                  />
                </div>
              ))}
            </div>

            <div style={S.actions}>
              <button style={{ ...S.btn, ...S.btnPrimary }} disabled={busy} onClick={save}>
                {isEditing ? 'Update' : 'Create'}
              </button>
              <button
                style={{ ...S.btn, ...S.btnDanger, opacity: form.id ? 1 : 0.4 }}
                disabled={busy || !form.id}
                onClick={remove}
              >
                Delete
              </button>
              <span style={S.msg}>{msg}</span>
            </div>

            <div style={S.note}>
              Saved to <code>drone_profiles.json</code> · compiled to MuJoCo by the
              Python backend (<code>generate_mjcf</code>). Assign profiles to
              recon/kami at env init.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const S = {
  overlay: { position: 'fixed', inset: 0, zIndex: 100, background: 'rgba(4,7,11,0.7)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  modal: { width: 860, maxWidth: '94vw', maxHeight: '88vh', background: '#0d1218', border: '1px solid #243042', borderRadius: 12, boxShadow: '0 20px 70px rgba(0,0,0,0.6)', color: '#e6e6e6', fontFamily: 'Segoe UI, system-ui, sans-serif', overflow: 'hidden', display: 'flex', flexDirection: 'column' },
  header: { display: 'flex', alignItems: 'baseline', gap: 10, padding: '14px 18px', borderBottom: '1px solid #1f2933' },
  title: { fontWeight: 800, letterSpacing: 1.5, color: '#7cc4ff', fontSize: 18 },
  sub: { color: '#6b7785', fontSize: 12, flex: 1 },
  close: { background: 'transparent', border: 'none', color: '#8c97a3', fontSize: 18, cursor: 'pointer' },
  body: { display: 'grid', gridTemplateColumns: '260px 1fr', gap: 0, minHeight: 0 },
  list: { borderRight: '1px solid #1f2933', padding: 12, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 },
  newBtn: { background: '#16212e', color: '#7cc4ff', border: '1px dashed #2f6da3', borderRadius: 6, padding: '8px', cursor: 'pointer', fontWeight: 700, fontSize: 12 },
  listItem: { background: 'rgba(8,12,17,0.7)', border: '1px solid #161d25', borderRadius: 6, padding: '8px 10px', cursor: 'pointer' },
  listItemActive: { border: '1px solid #2f6da3', boxShadow: 'inset 0 0 0 1px #2f6da3', background: '#13202d' },
  listName: { fontSize: 13, fontWeight: 700, color: '#dbe6f2' },
  listMeta: { fontSize: 11, color: '#6b7785', marginTop: 2 },
  dim: { color: '#5b6675', fontSize: 12, padding: 8 },
  formCol: { padding: 18, overflowY: 'auto' },
  lbl: { display: 'block', fontSize: 11, letterSpacing: 0.5, color: '#6b7785', textTransform: 'uppercase', margin: '10px 0 4px' },
  input: { width: '100%', background: '#0a0e13', border: '1px solid #243042', borderRadius: 6, color: '#e6e6e6', padding: '8px 10px', fontSize: 13 },
  topoRow: { display: 'flex', gap: 6 },
  topoBtn: { flex: 1, background: 'transparent', color: '#8c97a3', border: '1px solid #243042', borderRadius: 6, padding: '8px', cursor: 'pointer', fontSize: 12, fontWeight: 600 },
  topoBtnActive: { background: '#13202d', color: '#7cc4ff', boxShadow: 'inset 0 0 0 1px #2f6da3' },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 14px' },
  actions: { display: 'flex', alignItems: 'center', gap: 10, marginTop: 18 },
  btn: { border: 'none', borderRadius: 6, padding: '9px 18px', cursor: 'pointer', fontWeight: 700, fontSize: 13 },
  btnPrimary: { background: '#2f6da3', color: '#fff' },
  btnDanger: { background: '#3a1d22', color: '#ff7875', border: '1px solid #5a2a30' },
  msg: { color: '#52c41a', fontSize: 12 },
  note: { marginTop: 16, fontSize: 11, color: '#5b6675', lineHeight: 1.5 },
};
