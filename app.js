const uploadInput = document.getElementById("fileInput");
const loaderSelect = document.getElementById("loaderSelect");
const versionSelect = document.getElementById("versionSelect");

const inspectBtn = document.getElementById("inspectBtn");
const updateBtn = document.getElementById("updateBtn");

const customMinecraftInput = document.getElementById("customMinecraftInput");
const customNeoForgeInput = document.getElementById("customNeoForgeInput");
const customJavafmlInput = document.getElementById("customJavafmlInput");
const dependencyOverridesList = document.getElementById("dependencyOverridesList");
const addOverrideBtn = document.getElementById("addOverrideBtn");

const statusBox = document.getElementById("statusBox");
const progressList = document.getElementById("progressList");

const resultPanel = document.getElementById("resultPanel");
const resultBody = document.getElementById("resultBody");
const errorBox = document.getElementById("errorBox");

const consolePanel = document.getElementById("consolePanel");
const consoleOutput = document.getElementById("consoleOutput");


let currentFileId = null;
let currentFileIds = [];
let lastInspection = null;


// -----------------------------
// Helpers
// -----------------------------

function setStatus(message) {
    if (statusBox)
        statusBox.textContent = message;
}


function escapeHTML(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function appendConsole(message, type="info") {

    if (!consoleOutput || !consolePanel)
        return;

    consolePanel.style.display = "block";

    const prefix =
        type === "error" ? "[ERROR]" :
        type === "warn" ? "[WARN]" :
        "[INFO]";


    const line = `${prefix} ${message}`;


    if (
        consoleOutput.textContent ===
        "No console output yet."
    ) {
        consoleOutput.textContent = line;
    }
    else {
        consoleOutput.textContent += "\n" + line;
    }
}


function clearConsole() {

    if (!consoleOutput)
        return;

    consoleOutput.textContent =
        "No console output yet.";

    if(consolePanel)
        consolePanel.style.display="none";
}


function showError(message){

    if(!errorBox)
        return;

    resultPanel.style.display="block";

    errorBox.style.display="block";
    errorBox.textContent=message;
}


function clearError(){

    if(!errorBox)
        return;

    errorBox.style.display="none";
    errorBox.textContent="";
}


function addDependencyOverride(modId="", versionRange="") {
    if (!dependencyOverridesList)
        return;

    const row = document.createElement("div");
    row.className = "override-row";
    row.style.cssText = "display:flex; gap:8px; align-items:center;";

    const idInput = document.createElement("input");
    idInput.type = "text";
    idInput.className = "override-mod-id";
    idInput.placeholder = "mod id";
    idInput.value = modId;

    const rangeInput = document.createElement("input");
    rangeInput.type = "text";
    rangeInput.className = "override-version-range";
    rangeInput.placeholder = "version range, e.g. [21.1,)";
    rangeInput.value = versionRange;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "secondary";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", () => row.remove());

    row.append(idInput, rangeInput, removeBtn);
    dependencyOverridesList.appendChild(row);
}



async function parseResponse(response){

    const text = await response.text();

    let json=null;

    try{
        json=JSON.parse(text);
    }
    catch{}

    return {
        text,
        json
    };
}



// -----------------------------
// Progress
// -----------------------------

function renderProgress(steps,current){

    if(!progressList)
        return;


    progressList.innerHTML="";


    steps.forEach((step,index)=>{

        const div=document.createElement("div");

        div.className="progress-item";


        if(index < current)
            div.classList.add("done");


        if(index===current)
            div.classList.add("active");


        div.textContent=step;


        progressList.appendChild(div);

    });
}



function startProgress(steps){

    let index=0;

    renderProgress(steps,index);


    return setInterval(()=>{

        index++;

        if(index < steps.length)
            renderProgress(steps,index);

    },700);

}



// -----------------------------
// Upload
// -----------------------------

async function uploadFile(file = uploadInput?.files?.[0]){

    if(
        !uploadInput ||
        !uploadInput.files ||
        !file
    ){
        throw new Error(
            "Choose a .jar or .zip file first."
        );
    }


    const form=new FormData();

    form.append(
        "file",
        file
    );


    const response=await fetch(
        "/upload",
        {
            method:"POST",
            body:form
        }
    );


    const data=await parseResponse(response);


    if(!response.ok){

        appendConsole(
            data.text,
            "error"
        );

        throw new Error(
            data.json?.detail ||
            data.json?.message ||
            data.text ||
            "Upload failed"
        );
    }


    return data.json;
}


async function uploadSelectedFiles() {
    const files = Array.from(uploadInput?.files || []);
    if (!files.length)
        throw new Error("Choose one or more .jar or .zip files first.");
    return Promise.all(files.map(file => uploadFile(file)));
}



// -----------------------------
// Inspect
// -----------------------------

async function inspectFile(){

    const steps=[
        "Uploading...",
        "Inspecting Problems...",
        "Reading Metadata...",
        "Checking Compatibility...",
        "Generating Report..."
    ];


    clearConsole();
    clearError();


    const timer=startProgress(steps);
    if (inspectBtn) inspectBtn.disabled = true;
    if (updateBtn) updateBtn.disabled = true;


    try{

        setStatus(
            "Starting inspection..."
        );


        const uploads = await uploadSelectedFiles();
        const upload = uploads[0];


        currentFileId=
            upload.file_id;
        currentFileIds = uploads.map(item => item.file_id);



        appendConsole(
            `Uploaded ${uploads.length} archive(s). Primary file id: ${currentFileId}`
        );



        const response=
            await fetch(
                "/inspect",
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                        "application/json"
                    },


                    body:JSON.stringify({

                        file_id:
                        currentFileId,


                        loader:
                        loaderSelect.value,


                        minecraft_version:
                        versionSelect.value==="auto"
                        ?
                        null
                        :
                        versionSelect.value

                    })
                }
            );



        const data=
            await parseResponse(response);



        if(!response.ok)
            throw new Error(
                data.json?.detail ||
                data.text ||
                "Inspection failed"
            );



        lastInspection=data.json;


        renderReport(
            lastInspection
        );


        setStatus(
            "Inspection complete."
        );


    }
    catch(err){

        showError(err.message);

        appendConsole(
            err.stack ||
            err.message,
            "error"
        );

        setStatus(
            err.message
        );

    }
    finally{

        clearInterval(timer);

        renderProgress(
            steps,
            steps.length-1
        );
        if (inspectBtn) inspectBtn.disabled = false;
        if (updateBtn) updateBtn.disabled = false;

    }

}



// -----------------------------
// Job polling
// -----------------------------

async function pollJobStatus(jobId, { intervalMs = 1200, timeoutMs = 10 * 60 * 1000 } = {}) {
    const start = Date.now();

    while (true) {
        const response = await fetch(`/status/${jobId}`);
        const data = await parseResponse(response);

        if (!response.ok) {
            throw new Error(
                data.json?.detail ||
                data.text ||
                "Failed to check job status."
            );
        }

        const job = data.json;

        if (job.status === "completed") {
            return job;
        }

        if (job.status === "failed") {
            throw new Error(job.error || "Update job failed.");
        }

        if (Date.now() - start > timeoutMs) {
            throw new Error("Timed out waiting for the port to finish.");
        }

        await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
}


// -----------------------------
// Update
// -----------------------------

async function updateFile(){

    if(!currentFileIds.length){

        await inspectFile();


        if(!currentFileId)
            return;
    }



    clearConsole();
    clearError();


    setStatus(
        "Updating metadata..."
    );



    try{

        if (updateBtn) updateBtn.disabled = true;


        const overrides = {};
        if (dependencyOverridesList) {
            const rows = dependencyOverridesList.querySelectorAll(".override-row");
            rows.forEach(row => {
                const modId = row.querySelector(".override-mod-id").value.trim();
                const range = row.querySelector(".override-version-range").value.trim();
                if (modId && range) {
                    overrides[modId] = range;
                }
            });
        }

        const isBatch = currentFileIds.length > 1;
        const response=
            await fetch(
                isBatch ? "/update-batch" : "/update",
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                        "application/json"
                    },

                    body:JSON.stringify({
                        ...(isBatch
                            ? { file_ids: currentFileIds }
                            : { file_id: currentFileId, loader: loaderSelect.value }),
                        target_version: versionSelect.value==="auto" ? null : versionSelect.value,
                        custom_neoforge_version: customNeoForgeInput && customNeoForgeInput.value.trim() || null,
                        custom_javafml_version: customJavafmlInput && customJavafmlInput.value.trim() || null,
                        custom_minecraft_version: customMinecraftInput && customMinecraftInput.value.trim() || null,
                        dependency_overrides: Object.keys(overrides).length > 0 ? overrides : null
                    })
                }
            );



        const data=
            await parseResponse(response);


        if(!response.ok){

            throw new Error(
                data.json?.detail ||
                data.text ||
                "Update failed"
            );
        }


        // /update and /update-batch only hand back a job_id; the actual
        // download_url/filename are written to the job once the background
        // task finishes. Poll /status/{job_id} until it's done before
        // touching the download link, otherwise download_url/filename are
        // undefined and the browser saves a bogus "undefined.json".
        const jobId = data.json.job_id;

        setStatus(
            isBatch
                ? "Porting dependent mod bundle..."
                : "Porting mod..."
        );

        const result = await pollJobStatus(jobId);

        // Trigger a plain GET navigation download instead of fetching the
        // bytes ourselves and wrapping them in a blob: URL. Browsers treat
        // blob-URL downloads of "dangerous" extensions like .jar with extra
        // suspicion and will silently rename them (e.g. appending "_") until
        // the user confirms them. A normal server-backed download link does
        // not trigger that behavior.
        const a=
            document.createElement("a");


        a.href=result.download_url;
        a.download=result.filename;


        document.body.appendChild(a);

        a.click();


        a.remove();



        const mergedResult = { ...(lastInspection || {}), ...result };
        renderReport(mergedResult);

        setStatus(
            isBatch
                ? "Dependent mod bundle updated and downloaded."
                : "Port updated and downloaded."
        );


        appendConsole(
            "Update completed successfully."
        );

        const changedFiles = Array.isArray(result.changed_files)
            ? result.changed_files
            : [];
        if (changedFiles.length) {
            appendConsole(`Changed ${changedFiles.length} file(s): ${changedFiles.join(", ")}`);
        } else {
            appendConsole("No source or metadata changes matched the selected migration rules.", "warn");
        }

        const unresolvedIssues = Array.isArray(result.unresolved_issues)
            ? result.unresolved_issues
            : [];
        unresolvedIssues.forEach(issue => appendConsole(issue, "warn"));

        const missingDependencies = Array.isArray(result.missing_dependencies)
            ? result.missing_dependencies
            : [];
        missingDependencies.forEach(modId => appendConsole(`Missing from uploaded batch: ${modId}`, "warn"));


    }
    catch(err){

        showError(err.message);

        appendConsole(
            err.stack ||
            err.message,
            "error"
        );

        setStatus(
            err.message
        );

    }
    finally {
        if (updateBtn) updateBtn.disabled = false;
    }

}



// -----------------------------
// Report
// -----------------------------

function list(items){

    if(!items || items.length===0)
        return "<li>None</li>";


    return items
        .map(
            x=>`<li>${escapeHTML(x)}</li>`
        )
        .join("");

}

function renderDependencies(data) {
    if (data.dependency_analysis && data.dependency_analysis.resolutions && data.dependency_analysis.resolutions.length > 0) {
        const res = data.dependency_analysis.resolutions;
        return "<ul>" + res.map(r => {
            if (r.status === "RESOLVED") {
                return `<li>✅ <strong>${escapeHTML(r.mod_id)}</strong>: Resolved to v${escapeHTML(r.resolved_version)} <em>(Range: <code>${escapeHTML(r.version_range)}</code>)</em></li>`;
            } else {
                return `<li>❌ <strong>${escapeHTML(r.mod_id)}</strong>: Unresolved (${escapeHTML(r.status)})</li>`;
            }
        }).join("") + "</ul>";
    } else if (data.discovered_dependencies && data.discovered_dependencies.length > 0) {
        return "<ul>" + data.discovered_dependencies.map(d => `<li>🔍 Found: <strong>${escapeHTML(d)}</strong> (Will resolve on port)</li>`).join("") + "</ul>";
    }
    return "<ul><li>None</li></ul>";
}



function renderReport(data){

    resultPanel.style.display="block";


    resultBody.innerHTML=`

<div class="result-grid">

<div class="result-item">
<strong>Status</strong>
<span>${escapeHTML(data.compatibility_status || "Unknown")}</span>
</div>


<div class="result-item">
<strong>Loader</strong>
<span>${escapeHTML(data.loader || "Unknown")}</span>
</div>


<div class="result-item">
<strong>Current Version</strong>
<span>${escapeHTML(data.current_version || "Unknown")}</span>
</div>


<div class="result-item">
<strong>Target Version</strong>
<span>${escapeHTML(data.target_version || "Auto")}</span>
</div>


</div>


<h3>Problems</h3>
<ul>${list(data.problems)}</ul>


<h3>Suggested Fixes</h3>
<ul>${list(data.suggested_fixes)}</ul>


<h3>Changed Files</h3>
<ul>${list(data.changed_files)}</ul>


<h3>Dependencies</h3>
${renderDependencies(data)}


<h3>Archive Summary</h3>
<ul>${list(Object.entries(data.file_summary || {}).map(([name, count]) => `${name.replaceAll("_", " ")}: ${count}`))}</ul>


<h3>Limitations</h3>
<ul>${list(data.known_limitations)}</ul>

`;

}



// -----------------------------
// Events
// -----------------------------

if(inspectBtn)
    inspectBtn.onclick=inspectFile;


if(updateBtn)
    updateBtn.onclick=updateFile;


if(addOverrideBtn)
    addOverrideBtn.addEventListener("click", () => addDependencyOverride());


if(resultPanel)
    resultPanel.style.display="none";