import React from 'react';

export default function BottomPanel({ gameState, sendCommand }) {
  const handlePauseResume = () => {
    if (!gameState) return;
    if (gameState.paused) {
      sendCommand({ type: 'RESUME' });
    } else {
      sendCommand({ type: 'PAUSE' });
    }
  };

  return (
    <div className="flex items-center justify-between px-6 h-full text-sm">
      <div className="flex space-x-4">
        {/* Placeholder for selected asset actions */}
        <span className="text-gray-500 italic">No asset selected</span>
      </div>

      <div className="flex space-x-4 items-center">
        <button 
          onClick={() => sendCommand({ type: 'RESET' })}
          disabled={!gameState}
          className={`px-6 py-2 font-bold rounded shadow-lg transition-colors ${
            !gameState ? 'bg-gray-700 text-gray-500 cursor-not-allowed' :
            'bg-rose-700 hover:bg-rose-600 text-white'
          }`}
        >
          맵 리스트로 복귀
        </button>
        <button 
          onClick={() => {
            const currentSpeed = gameState.speed || 1.0;
            sendCommand({ type: 'SET_SPEED', speed: currentSpeed === 1.0 ? 4.0 : 1.0 });
          }}
          disabled={!gameState}
          className={`px-6 py-2 font-bold rounded shadow-lg transition-colors ${
            !gameState ? 'bg-gray-700 text-gray-500 cursor-not-allowed' :
            (gameState.speed === 4.0) ? 'bg-indigo-600 hover:bg-indigo-500 text-white' : 'bg-gray-700 hover:bg-gray-600 text-white'
          }`}
        >
          {gameState && gameState.speed === 4.0 ? '⏩ 4X SPEED' : '▶ 1X SPEED'}
        </button>
        <button 
          onClick={handlePauseResume}
          disabled={!gameState}
          className={`px-6 py-2 font-bold rounded shadow-lg transition-colors ${
            !gameState ? 'bg-gray-700 text-gray-500 cursor-not-allowed' :
            gameState.paused ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-amber-600 hover:bg-amber-500 text-white'
          }`}
        >
          {gameState && gameState.paused ? 'RESUME SIMULATION' : 'PAUSE SIMULATION'}
        </button>
      </div>
    </div>
  );
}
