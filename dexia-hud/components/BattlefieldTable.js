// Battlefield Table panel — pre-designate a named map area, list saved tables,
// and reopen one to restore its remembered enemy / friendly layout to the sim.
//
// Flow:
//   [+ 새 전장 테이블] -> name + size -> [지도에서 중심 지정] (parent enters
//   pick mode; next map click sets the centre and creates the table) -> the
//   table is now ACTIVE: placing 적 진지 / 아군 진영 is auto-remembered into it.
//   [열기] reloads a table: map flies to its area + saved positions are restored.

import { useState } from 'react';

export default function BattlefieldTable({
  open, tables = [], activeId, picking, onStartPick, onOpen, onDelete, onClose,
}) {
  const [name, setName] = useState('');
  const [extent, setExtent] = useState(20);
  const [adding, setAdding] = useState(false);

  if (!open) return null;

  const startPick = () => {
    const nm = name.trim() || `전장 ${tables.length + 1}`;
    onStartPick({ name: nm, extent_m: Number(extent) });
    setName('');
    setAdding(false);
  };

  return (
    <div style={S.panel}>
      <div style={S.head}>
        <span style={S.title}>▦ 전장 테이블</span>
        <button style={S.x} onClick={onClose}>✕</button>
      </div>

      {picking && (
        <div style={S.pickHint}>🎯 지도에서 전장 <b>중심점</b>을 클릭하세요…</div>
      )}

      {!adding ? (
        <button style={S.addBtn} onClick={() => setAdding(true)} disabled={picking}>+ 새 전장 테이블</button>
      ) : (
        <div style={S.form}>
          <input
            style={S.input}
            placeholder="테이블 이름 (예: 1구역 SEAD)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <label style={S.sliderRow}>
            <span style={S.sliderLabel}>면적(반경) {extent}m</span>
            <input type="range" min={5} max={80} step={5} value={extent}
                   onChange={(e) => setExtent(e.target.value)} style={S.slider} />
          </label>
          <div style={S.formBtns}>
            <button style={S.pickBtn} onClick={startPick}>지도에서 중심 지정 →</button>
            <button style={S.cancelBtn} onClick={() => setAdding(false)}>취소</button>
          </div>
        </div>
      )}

      <div style={S.listTitle}>저장된 테이블 ({tables.length})</div>
      <div style={S.list}>
        {tables.length === 0 && <div style={S.dim}>저장된 전장 테이블이 없습니다.</div>}
        {tables.map((t) => {
          const isActive = t.id === activeId;
          return (
            <div key={t.id} style={{ ...S.row, ...(isActive ? S.rowActive : {}) }}>
              <div style={S.rowMain}>
                <div style={S.rowName}>
                  {isActive ? '● ' : ''}{t.name}
                </div>
                <div style={S.rowMeta}>
                  중심 [{t.center?.[0]?.toFixed?.(0)}, {t.center?.[1]?.toFixed?.(0)}] · 반경 {t.extent_m}m
                  {' · '}
                  {t.enemy ? '적✓' : '적—'} {t.friendly ? '아군✓' : '아군—'}
                </div>
                <div style={S.rowTime}>{(t.updated_at || '').slice(0, 19).replace('T', ' ')}</div>
              </div>
              <div style={S.rowBtns}>
                <button style={S.openBtn} onClick={() => onOpen(t)}>열기</button>
                <button style={S.delBtn} onClick={() => onDelete(t.id)}>삭제</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const S = {
  panel: { position: 'absolute', top: 56, left: '50%', transform: 'translateX(-50%)', zIndex: 46, width: 380, maxWidth: '94vw', background: 'rgba(13,18,24,0.96)', border: '1px solid #2f6da3', borderRadius: 10, padding: 14, boxShadow: '0 10px 36px rgba(0,0,0,0.6)', pointerEvents: 'auto' },
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, paddingBottom: 6, borderBottom: '1px solid #1f2933' },
  title: { color: '#7cc4ff', fontWeight: 800, fontSize: 13, letterSpacing: 1 },
  x: { background: 'transparent', border: 'none', color: '#8c97a3', fontSize: 14, cursor: 'pointer' },
  pickHint: { background: 'rgba(40,28,12,0.6)', border: '1px solid #faad14', borderRadius: 6, padding: '7px 10px', fontSize: 12, color: '#ffd7a8', marginBottom: 10 },
  addBtn: { width: '100%', background: '#1f7a3d', color: '#fff', border: 'none', borderRadius: 6, padding: '8px', fontWeight: 700, fontSize: 12.5, cursor: 'pointer', marginBottom: 10 },
  form: { background: 'rgba(8,12,17,0.7)', border: '1px solid #243042', borderRadius: 7, padding: 10, marginBottom: 10, display: 'flex', flexDirection: 'column', gap: 8 },
  input: { background: '#0a0e13', color: '#e6e6e6', border: '1px solid #243042', borderRadius: 5, padding: '7px 9px', fontSize: 12.5 },
  sliderRow: { display: 'flex', flexDirection: 'column', gap: 4 },
  sliderLabel: { fontSize: 11, color: '#9fd0ff' },
  slider: { width: '100%' },
  formBtns: { display: 'flex', gap: 6 },
  pickBtn: { flex: 1, background: '#2f6da3', color: '#fff', border: 'none', borderRadius: 5, padding: '7px', fontWeight: 700, fontSize: 12, cursor: 'pointer' },
  cancelBtn: { background: 'transparent', color: '#8c97a3', border: '1px solid #243042', borderRadius: 5, padding: '7px 11px', fontSize: 12, cursor: 'pointer' },
  listTitle: { fontSize: 10.5, letterSpacing: 1, color: '#6b7785', textTransform: 'uppercase', margin: '4px 0 6px' },
  list: { display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 300, overflowY: 'auto' },
  dim: { color: '#5b6675', fontSize: 12, padding: '4px 0' },
  row: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, background: 'rgba(8,12,17,0.7)', border: '1px solid #161d25', borderRadius: 6, padding: '8px 10px' },
  rowActive: { boxShadow: 'inset 0 0 0 1px #52c41a', borderColor: '#1f7a3d' },
  rowMain: { minWidth: 0, flex: 1 },
  rowName: { fontSize: 12.5, fontWeight: 700, color: '#dbe6f2' },
  rowMeta: { fontSize: 10.5, color: '#8c97a3', marginTop: 2 },
  rowTime: { fontSize: 9.5, color: '#5b6675', marginTop: 1 },
  rowBtns: { display: 'flex', flexDirection: 'column', gap: 4 },
  openBtn: { background: '#2f6da3', color: '#fff', border: 'none', borderRadius: 4, padding: '4px 12px', fontSize: 11, fontWeight: 700, cursor: 'pointer' },
  delBtn: { background: 'transparent', color: '#ff7875', border: '1px solid #5a2a30', borderRadius: 4, padding: '4px 12px', fontSize: 11, cursor: 'pointer' },
};
