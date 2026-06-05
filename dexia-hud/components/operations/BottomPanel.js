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
