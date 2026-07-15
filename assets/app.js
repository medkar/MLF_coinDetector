// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

let errorContainer = document.getElementById('error-container');
const recentDetectionsElement = document.getElementById('recentDetections');
const feedbackContentElement = document.getElementById('feedback-content');
const MAX_RECENT_SCANS = 5;
let scans = [];

const ui = new WebUI();
ui.on_connect(onUIConnected);
ui.on_disconnect(onUIDisconnected);
ui.on_message('detection', async message => {
  printDetection(message);
  renderDetections();
  updateFeedback(message);
});

// Start the application
initializeConfidenceSlider();
updateFeedback(null);
renderDetections();

// Popover logic
const confidencePopoverText =
  'Minimum confidence score for detected objects. Lower values show more results but may include false positives.';
const feedbackPopoverText =
  'When the camera detects an object like cat, cell phone, clock, cup, dog or potted plant, a picture and a message will be shown here.';

document.querySelectorAll('.info-btn.confidence').forEach(img => {
  const popover = img.nextElementSibling;
  img.addEventListener('mouseenter', () => {
    popover.textContent = confidencePopoverText;
    popover.style.display = 'block';
  });
  img.addEventListener('mouseleave', () => {
    popover.style.display = 'none';
  });
});

document.querySelectorAll('.info-btn.feedback').forEach(img => {
  const popover = img.nextElementSibling;
  img.addEventListener('mouseenter', () => {
    popover.textContent = feedbackPopoverText;
    popover.style.display = 'block';
  });
  img.addEventListener('mouseleave', () => {
    popover.style.display = 'none';
  });
});

function onUIConnected() {
  if (errorContainer) {
    errorContainer.style.display = 'none';
    errorContainer.textContent = '';
  }
}

function onUIDisconnected() {
  if (errorContainer) {
    errorContainer.textContent = 'Connection to the board lost. Please check the connection.';
    errorContainer.style.display = 'block';
  }
}

function updateFeedback(detection) {
  const objectInfo = {
    cat: { text: 'Meow!', gif: 'cat.webp' },
    'cell phone': { text: 'Stay connected', gif: 'phone.webp' },
    clock: { text: 'Time to go', gif: 'clock.webp' },
    cup: { text: 'Need a break?', gif: 'cup.webp' },
    dog: { text: 'Walkies?', gif: 'dog.webp' },
    'potted plant': { text: 'Glow your ideas!', gif: 'plant.webp' },
  };

  if (detection && objectInfo[detection.content]) {
    const info = objectInfo[detection.content];
    const confidence = Math.floor(detection.confidence * 100);
    feedbackContentElement.innerHTML = `
            <div class="feedback-detection">
                <div class="percentage">${confidence}%</div>
                <img src="img/${info.gif}" alt="${detection.content}">
                <p>${info.text}</p>
            </div>
        `;
  } else {
    feedbackContentElement.innerHTML = `
            <img src="img/stars.svg" alt="Stars">
            <p class="feedback-text">System response will appear here</p>
        `;
  }
}

function printDetection(newDetection) {
  scans.unshift(newDetection);
  if (scans.length > MAX_RECENT_SCANS) {
    scans.pop();
  }
}

// Function to render the list of scans
function renderDetections() {
  // Clear the list
  recentDetectionsElement.innerHTML = ``;

  if (scans.length === 0) {
    recentDetectionsElement.innerHTML = `
            <div class="no-recent-scans">
                <img src="./img/no-face.svg">
                No object detected yet
            </div>
        `;
    return;
  }

  scans.forEach(scan => {
    const row = document.createElement('div');
    row.className = 'scan-container';

    // Create a container for content and time
    const cellContainer = document.createElement('span');
    cellContainer.className = 'scan-cell-container cell-border';

    // Content (text + icon)
    const contentText = document.createElement('span');
    contentText.className = 'scan-content';
    const value = scan.confidence;
    const result = Math.floor(value * 1000) / 10;
    const pos = scan.X != null && scan.Y != null ? ` — (${scan.X}, ${scan.Y}) mm` : '';
    contentText.innerHTML = `${result}% - ${scan.content}${pos}`;

    // Time
    const timeText = document.createElement('span');
    timeText.className = 'scan-content-time';
    timeText.textContent = new Date(scan.timestamp).toLocaleString('it-IT').replace(',', ' -');

    // Append content and time to the container
    cellContainer.appendChild(contentText);
    cellContainer.appendChild(timeText);

    row.appendChild(cellContainer);
    recentDetectionsElement.appendChild(row);
  });
}

function initializeConfidenceSlider() {
  const confidenceSlider = document.getElementById('confidenceSlider');
  const confidenceInput = document.getElementById('confidenceInput');
  const confidenceResetButton = document.getElementById('confidenceResetButton');

  confidenceSlider.addEventListener('input', updateConfidenceDisplay);
  confidenceInput.addEventListener('input', handleConfidenceInputChange);
  confidenceInput.addEventListener('blur', validateConfidenceInput);
  updateConfidenceDisplay();

  confidenceResetButton.addEventListener('click', e => {
    if (e.target.classList.contains('reset-icon') || e.target.closest('.reset-icon')) {
      resetConfidence();
    }
  });
}

function handleConfidenceInputChange() {
  const confidenceInput = document.getElementById('confidenceInput');
  const confidenceSlider = document.getElementById('confidenceSlider');

  let value = parseFloat(confidenceInput.value);

  if (isNaN(value)) value = 0.5;
  if (value < 0) value = 0;
  if (value > 1) value = 1;

  confidenceSlider.value = value;
  updateConfidenceDisplay();
}

function validateConfidenceInput() {
  const confidenceInput = document.getElementById('confidenceInput');
  let value = parseFloat(confidenceInput.value);

  if (isNaN(value)) value = 0.5;
  if (value < 0) value = 0;
  if (value > 1) value = 1;

  confidenceInput.value = value.toFixed(2);

  handleConfidenceInputChange();
}

function updateConfidenceDisplay() {
  const confidenceSlider = document.getElementById('confidenceSlider');
  const confidenceInput = document.getElementById('confidenceInput');
  const confidenceValueDisplay = document.getElementById('confidenceValueDisplay');
  const sliderProgress = document.getElementById('sliderProgress');

  const value = parseFloat(confidenceSlider.value);
  ui.send_message('override_th', value); // Send confidence to backend
  const percentage = ((value - confidenceSlider.min) / (confidenceSlider.max - confidenceSlider.min)) * 100;

  const displayValue = value.toFixed(2);
  confidenceValueDisplay.textContent = displayValue;

  if (document.activeElement !== confidenceInput) {
    confidenceInput.value = displayValue;
  }

  sliderProgress.style.width = percentage + '%';
  confidenceValueDisplay.style.left = percentage + '%';
}

function resetConfidence() {
  const confidenceSlider = document.getElementById('confidenceSlider');
  const confidenceInput = document.getElementById('confidenceInput');

  confidenceSlider.value = '0.5';
  confidenceInput.value = '0.50';
  updateConfidenceDisplay();
}

// --- Calibration (étapes A + B) : capture, clic des 4 coins -> homographie, test mm ---
const calibCaptureBtn = document.getElementById('calibCaptureBtn');
const calibResetBtn = document.getElementById('calibResetBtn');
const calibSquareInput = document.getElementById('calibSquareInput');
const calibCanvas = document.getElementById('calibCanvas');
const calibStatus = document.getElementById('calibStatus');
const calibResult = document.getElementById('calibResult');

let calibImage = null; // Image capturée
let calibPoints = []; // 4 coins cliqués [[u,v], ...]
let calibrated = false; // homographie disponible ?
let testPoints = []; // points de test { u, v, X, Y }

const CORNER_LABELS = ['haut-gauche', 'haut-droit', 'bas-droit', 'bas-gauche'];

function drawMarker(ctx, u, v, color, label) {
  ctx.beginPath();
  ctx.arc(u, v, 5, 0, 2 * Math.PI);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#000';
  ctx.stroke();
  ctx.font = '14px sans-serif';
  ctx.fillStyle = color;
  ctx.fillText(label, u + 8, v - 8);
}

function calibRedraw() {
  if (!calibCanvas) return;
  const ctx = calibCanvas.getContext('2d');
  ctx.clearRect(0, 0, calibCanvas.width, calibCanvas.height);
  if (calibImage) ctx.drawImage(calibImage, 0, 0, calibCanvas.width, calibCanvas.height);
  calibPoints.forEach((p, i) => drawMarker(ctx, p[0], p[1], '#00e676', String(i + 1)));
  testPoints.forEach(t => drawMarker(ctx, t.u, t.v, '#ffd600', `${t.X},${t.Y}mm`));
}

function canvasToPixel(e) {
  const rect = calibCanvas.getBoundingClientRect();
  return [
    (e.clientX - rect.left) * (calibCanvas.width / rect.width),
    (e.clientY - rect.top) * (calibCanvas.height / rect.height),
  ];
}

if (calibCaptureBtn) {
  calibCaptureBtn.addEventListener('click', () => {
    calibStatus.textContent = 'Capture en cours…';
    ui.send_message('calib_capture', {});
  });
}

if (calibResetBtn) {
  calibResetBtn.addEventListener('click', () => {
    calibPoints = [];
    calibrated = false;
    testPoints = [];
    calibResult.textContent = '';
    calibStatus.textContent = 'Clique les 4 coins : ' + CORNER_LABELS.join(', ') + '.';
    calibRedraw();
  });
}

if (calibCanvas) {
  calibCanvas.addEventListener('click', e => {
    if (!calibImage) {
      calibStatus.textContent = 'Capture d’abord une image.';
      return;
    }
    const [u, v] = canvasToPixel(e);
    if (calibPoints.length < 4) {
      calibPoints.push([u, v]);
      calibRedraw();
      if (calibPoints.length < 4) {
        calibStatus.textContent = `Point ${calibPoints.length}/4 placé. Suivant : ${CORNER_LABELS[calibPoints.length]}.`;
      } else {
        calibStatus.textContent = 'Calcul de l’homographie…';
        const square = parseFloat(calibSquareInput.value) || 174;
        ui.send_message('calib_compute', { points: calibPoints, square_mm: square });
      }
    } else if (calibrated) {
      ui.send_message('calib_test_point', { u, v });
    }
  });
}

ui.on_message('calib_frame', message => {
  if (!message || !message.ok) {
    calibStatus.textContent = 'Échec capture : ' + ((message && message.error) || 'inconnu');
    return;
  }
  const img = new Image();
  img.onload = () => {
    calibImage = img;
    calibPoints = [];
    calibrated = false;
    testPoints = [];
    calibResult.textContent = '';
    calibCanvas.width = message.w;
    calibCanvas.height = message.h;
    calibRedraw();
    calibStatus.textContent = `Image ${message.w}×${message.h}. Clique les 4 coins : ${CORNER_LABELS.join(', ')}.`;
  };
  img.src = message.img;
});

ui.on_message('calib_result', message => {
  if (!message || !message.ok) {
    calibrated = false;
    calibStatus.textContent = 'Échec calibration : ' + ((message && message.error) || 'inconnu');
    return;
  }
  calibrated = true;
  calibStatus.textContent = 'Calibration enregistrée. Clique n’importe où sur l’image pour tester (mm).';
  calibResult.textContent = `Erreur de reprojection max : ${message.error_mm} mm`;
});

ui.on_message('calib_test_result', message => {
  if (!message || !message.ok) return;
  testPoints.push({ u: message.u, v: message.v, X: message.X, Y: message.Y });
  calibRedraw();
});
