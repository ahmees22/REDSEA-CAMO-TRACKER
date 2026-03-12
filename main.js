const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');
const waitOn = require('wait-on');
const Store = require('electron-store');
const store = new Store();

let mainWindow;
let flaskProcess = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1300,
    height: 900,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    },
    icon: path.join(__dirname, 'icon.png'),
    title: 'Camo-Tracker Aviation Maintenance System'
  });

  mainWindow.setMenuBarVisibility(false);
  
  // Create loading window/HTML
  mainWindow.loadURL(`data:text/html;charset=utf-8,
    <html>
      <body style="background-color: %23111827; color: white; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh; font-family: sans-serif; margin: 0;">
        <h1 style="color: %23EF4444; font-size: 40px; margin-bottom: 20px;">Camo-Tracker Launcher</h1>
        <h3 style="color: %239CA3AF;">Initializing Database & Local Server...</h3>
        <p style="margin-top: 50px; color: %236B7280; font-size: 12px;">Please wait while the Engine spins up.</p>
      </body>
    </html>
  `);

  mainWindow.on('closed', function () {
    mainWindow = null;
  });
}

function startFlaskServer() {
  // Determine if in dev or prod packaged app
  let scriptPath;
  let pyExecutable;

  if (app.isPackaged) {
    // Packaged mode: Flask app is embedded 
    scriptPath = path.join(process.resourcesPath, 'app', 'app.py');
    // We assume python is bundled or available in system PATH
    pyExecutable = path.join(process.resourcesPath, 'python', 'python.exe'); 
    
    // Fallback if bundled python doesn't exist
    if(!fs.existsSync(pyExecutable)) {
      pyExecutable = 'python'; 
    }
  } else {
    // Dev mode
    scriptPath = path.join(__dirname, 'app.py');
    pyExecutable = path.join(__dirname, 'venv', 'Scripts', 'python.exe');
  }

  console.log(`Starting python server: ${pyExecutable} ${scriptPath}`);

  // Need to set an environment variable or flag to run differently in Electron if desired
  flaskProcess = spawn(pyExecutable, [scriptPath], {
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '0', FLASK_ENV: 'production' }
  });

  flaskProcess.stdout.on('data', (data) => {
    console.log(`Flask Out: ${data}`);
  });

  flaskProcess.stderr.on('data', (data) => {
    console.error(`Flask Err: ${data}`);
  });

  flaskProcess.on('close', (code) => {
    console.log(`Flask process exited with code ${code}`);
  });

  flaskProcess.on('error', (err) => {
    console.error('Failed to start python process', err);
    dialog.showErrorBox("Startup Error", "Failed to start local Python backend. Make sure Python is installed if not bundled.");
  });

  // Wait for Flask to boot on port 5000, then load page
  const opts = {
    resources: ['http-get://127.0.0.1:5000/login'],
    delay: 500, // initial delay
    interval: 250, // poll interval
    timeout: 30000, // 30 sec max wait
    window: 1000,
  };

  waitOn(opts)
    .then(() => {
      console.log('Flask is up. Loading UI...');
      if (mainWindow) {
        mainWindow.loadURL('http://127.0.0.1:5000/');
      }
    })
    .catch((err) => {
      console.error('Timeout waiting for Flask', err);
      dialog.showErrorBox("Backend Timeout", "The Python backend took too long to start. Please check the logs or restart the app.");
    });
}

app.whenReady().then(() => {
  createWindow();
  startFlaskServer();

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', function () {
  if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', () => {
  // Kill the flask child process when the app closes
  if (flaskProcess) {
    flaskProcess.kill('SIGINT');
  }
});
