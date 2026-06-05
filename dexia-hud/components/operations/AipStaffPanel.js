import React, { useState } from 'react';

export default function AipStaffPanel({ gameState, sendCommand }) {
  const [chatInput, setChatInput] = useState('');

  const handleApprove = (coaId) => {
    sendCommand({ type: 'APPROVE', coa_id: coaId });
  };

  const handleReject = (coaId) => {
    sendCommand({ type: 'REJECT', coa_id: coaId });
  };

  const pct = (v) => `${Math.round((v || 0) * 100)}%`;

  const handleModify = (e) => {
    e.preventDefault();
    if (!chatInput.trim()) return;
    sendCommand({ type: 'MODIFY', text: chatInput });
    setChatInput('');
  };

  return (
    <div className="flex flex-col h-full bg-gray-900 border-l border-gray-700 w-96 shrink-0 shadow-2xl overflow-hidden">
      
      {/* AIP Feed Section */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        <h2 className="text-xs text-gray-500 font-bold tracking-widest border-b border-gray-700 pb-2 mb-2 sticky top-0 bg-gray-900">AIP OPERATIONS FEED</h2>
        
        {gameState && gameState.feed && gameState.feed.map((msg, idx) => (
          <div key={idx} className="text-sm p-3 rounded bg-gray-800 border-l-2 border-cyan-500">
            <span className="text-xs font-bold text-cyan-400 block mb-1">[{msg.type}]</span>
            <span className="text-gray-300 whitespace-pre-line">{msg.message}</span>
          </div>
        ))}
      </div>

      {/* Approval Queue Section */}
      {gameState && gameState.approval_queue && gameState.approval_queue.length > 0 && (
        <div className="p-4 bg-gray-800/80 border-t border-rose-900/50 shadow-[0_-10px_20px_-5px_rgba(225,29,72,0.1)]">
          <h2 className="text-xs text-rose-400 font-bold tracking-widest mb-3 flex items-center">
            <span className="w-2 h-2 rounded-full bg-rose-500 animate-pulse mr-2"></span>
            APPROVAL REQUIRED
          </h2>
          
          <div className="space-y-3 max-h-72 overflow-y-auto">
            {gameState.approval_queue.map((coa, idx) => (
              <div key={idx} className={`bg-gray-900 border rounded p-3 text-sm ${
                coa.rank === 1 ? 'border-emerald-600/70' : 'border-gray-700'}`}>
                <div className="flex justify-between items-start mb-1">
                  <span className="font-bold text-gray-200">
                    {coa.rank ? `안${coa.rank}` : coa.id}
                    <span className="text-gray-500 font-normal ml-1">· {coa.action} → {coa.target || '구역'}</span>
                  </span>
                  {coa.rank === 1 && <span className="text-[10px] text-emerald-400 font-bold">AIP 추천</span>}
                </div>
                {/* kill vs loss balance */}
                <div className="flex gap-3 text-xs mb-2">
                  <span className="text-emerald-400">격파확률 {pct(coa.p_kill || coa.expected_success)}</span>
                  <span className="text-rose-400">아군손실 {pct(coa.est_loss)}</span>
                </div>
                <p className="text-gray-400 text-xs mb-3">{coa.description}</p>
                <div className="flex space-x-2">
                  <button onClick={() => handleApprove(coa.id)}
                    className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-1.5 rounded text-xs transition-colors">
                    승인 (APPROVE)
                  </button>
                  <button onClick={() => handleReject(coa.id)}
                    className="flex-1 bg-gray-700 hover:bg-rose-700 text-gray-200 font-bold py-1.5 rounded text-xs transition-colors">
                    거부 (REJECT)
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Chat / Modification Input */}
      <div className="p-4 border-t border-gray-700 bg-gray-900">
        <form onSubmit={handleModify} className="flex">
          <input 
            type="text" 
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            placeholder="Issue objective or modify COA..."
            className="flex-1 bg-gray-800 border border-gray-700 rounded-l px-3 py-2 text-sm text-white focus:outline-none focus:border-cyan-500"
          />
          <button type="submit" className="bg-gray-700 hover:bg-gray-600 text-gray-300 px-4 rounded-r text-sm font-bold border border-l-0 border-gray-700 transition-colors">
            SEND
          </button>
        </form>
      </div>
    </div>
  );
}
