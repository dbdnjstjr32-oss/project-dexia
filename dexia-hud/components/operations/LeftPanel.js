import React, { useState } from 'react';

const REGION_KO = {
  middle_east: '중동', korea: '한국', russia: '러시아',
  europe: '유럽', southeast_asia: '동남아',
};

export default function LeftPanel({ sendCommand, gameState, battlefields = [], info }) {
  const [region, setRegion] = useState('korea');
  const [activating, setActivating] = useState(false);

  const regions = [...new Set(battlefields.map((b) => b.region))];
  const maps = battlefields.filter((b) => b.region === region);

  const activate = (bf) => {
    sendCommand({ type: 'START', battlefield_id: bf.id });
    setActivating(true);
  };

  // ---- pre-game: battlefield picker ----
  if (!gameState) {
    return (
      <div className="p-4 flex flex-col h-full space-y-4">
        <div>
          <h2 className="text-sm text-cyan-400 font-bold mb-1 tracking-widest">전장 선택 (BATTLEFIELDS)</h2>
          <p className="text-xs text-gray-400">
            지역별 전장을 활성화하면 아군·적군 장비가 무작위 배치되고, 적군 지역은 정찰 전까지 가려집니다.
          </p>
        </div>

        {battlefields.length === 0 ? (
          <div className="text-xs text-amber-400">command_server 연결 대기 중… (목록 로딩)</div>
        ) : (
          <>
            <div className="flex flex-wrap gap-1">
              {regions.map((r) => (
                <button key={r} onClick={() => setRegion(r)}
                  className={`px-2 py-1 text-xs rounded font-bold ${
                    region === r ? 'bg-cyan-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}>
                  {REGION_KO[r] || r}
                </button>
              ))}
            </div>

            <div className="flex-1 overflow-y-auto space-y-1 pr-1">
              {maps.map((bf) => (
                <button key={bf.id} onClick={() => activate(bf)} disabled={activating}
                  className="w-full text-left p-2 bg-gray-800/60 hover:bg-cyan-900/40 border border-gray-700
                             rounded text-sm transition-colors disabled:opacity-50">
                  <div className="flex justify-between">
                    <span className="text-cyan-300 font-mono">{bf.name}</span>
                    <span className="text-gray-500 text-xs">{(bf.size_m / 1000).toFixed(0)} km · {bf.difficulty}</span>
                  </div>
                  <div className="text-gray-500 text-xs truncate">{bf.climate}</div>
                </button>
              ))}
            </div>
          </>
        )}

        {activating && (
          <div className="text-xs text-cyan-400 animate-pulse border-t border-gray-700 pt-2">
            ⟳ 전장 로딩 중… {info || ''}
          </div>
        )}
      </div>
    );
  }

  // ---- in-game: theatre info + assets + AIP tasking ----
  const bf = gameState.battlefield;
  return (
    <div className="p-4 flex flex-col h-full space-y-4">
      {bf && (
        <div className="bg-gray-800/60 border border-gray-700 rounded p-3">
          <div className="text-cyan-300 font-bold text-sm">{bf.name} <span className="text-gray-500">({REGION_KO[bf.region] || bf.region})</span></div>
          <div className="text-xs text-gray-400 mt-1">{bf.climate}</div>
          <div className="text-xs text-gray-500 mt-1">
            {bf.location[1].toFixed(2)}°, {bf.location[0].toFixed(2)}° · {(bf.size_m / 1000).toFixed(0)} km
          </div>
        </div>
      )}

      {/* AIP tasking — the live process buttons */}
      <div className="space-y-2">
        <h2 className="text-xs text-gray-500 font-bold tracking-widest border-b border-gray-700 pb-2">AIP 지시</h2>
        <button onClick={() => sendCommand({ type: 'RECON' })}
          className="w-full py-2 bg-sky-700 hover:bg-sky-600 text-white text-sm font-bold rounded">
          🛰 정찰 지시 (RECON)
        </button>
        <button onClick={() => sendCommand({ type: 'STRIKE_OPTIONS' })}
          className="w-full py-2 bg-rose-700 hover:bg-rose-600 text-white text-sm font-bold rounded">
          🎯 최적 타격전술 요청 (안1·2·3)
        </button>
        <button onClick={() => sendCommand({ type: 'BDA' })}
          className="w-full py-2 bg-amber-700 hover:bg-amber-600 text-white text-sm font-bold rounded">
          📋 재정찰 BDA 판정
        </button>
      </div>

      <div className="flex flex-col space-y-2">
        <h2 className="text-xs text-gray-500 font-bold tracking-widest border-b border-gray-700 pb-2">아군 자산</h2>
        <div className="space-y-1">
          {gameState.blue_details.map((b, i) => (
            <div key={i} className="flex justify-between items-center text-sm p-2 bg-gray-800/50 rounded">
              <span className="text-blue-300 font-mono">{b.id}</span>
              <span className="text-gray-400 text-xs">{b.cls.toUpperCase()}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="text-xs text-gray-400 p-2 bg-gray-800/50 rounded mt-auto">
        목표: 적 전력 탐지 및 격파. 아군 손실 5 이하 유지.
      </div>
    </div>
  );
}
