-- Nothing OS LIVE background: CPU-reactive dotted rings, a drifting dot field,
-- and sonar pings that quicken under load. Rides on the static wallpaper.
-- Requires conky built with Lua Cairo (conky-all).
pcall(function() require 'cairo' end)
pcall(function() require 'cairo_xlib' end)

local TWO_PI = 2 * math.pi
local seeded = false
local FIELD = {}   -- drifting dot field

function conky_bg()
    if conky_window == nil then return end
    local w, h = conky_window.width, conky_window.height
    if w == 0 or h == 0 then return end
    local cs = cairo_xlib_surface_create(conky_window.display, conky_window.drawable,
                                         conky_window.visual, w, h)
    local cr = cairo_create(cs)

    local t = tonumber(conky_parse('${time %s}')) or os.time()
    t = t + (tonumber(conky_parse('${time %N}')) or 0) / 1e9
    local cpu = tonumber(conky_parse('${cpu cpu0}')) or 0
    local load = cpu / 100                          -- 0..1, drives the "aliveness"
    local cx, cy = w * 0.5, h * 0.5

    -- seed the drifting dot field once
    if not seeded then
        for i = 1, 70 do
            FIELD[i] = { x = math.random() * w, y = math.random() * h,
                         a = math.random() * TWO_PI, sp = 4 + math.random() * 10,
                         red = (math.random() < 0.18) }
        end
        seeded = true
    end

    -- 1) drifting dot field (slow; faster + brighter when the CPU is busy)
    for _, d in ipairs(FIELD) do
        local spd = d.sp * (0.4 + load * 1.8)
        d.x = d.x + math.cos(d.a) * spd * 0.02
        d.y = d.y + math.sin(d.a) * spd * 0.02
        if d.x < 0 then d.x = d.x + w elseif d.x > w then d.x = d.x - w end
        if d.y < 0 then d.y = d.y + h elseif d.y > h then d.y = d.y - h end
        if d.red then cairo_set_source_rgba(cr, 0.84, 0.10, 0.13, 0.10 + 0.12 * load)
        else          cairo_set_source_rgba(cr, 0.80, 0.80, 0.85, 0.05 + 0.06 * load) end
        cairo_arc(cr, d.x, d.y, d.red and 1.6 or 1.2, 0, TWO_PI)
        cairo_fill(cr)
    end

    -- 2) concentric dotted rings, breathing (livelier with CPU)
    local rings = { 150, 270, 400, 540, 690, 850, 1010 }
    for i, r in ipairs(rings) do
        local breath = 0.5 + 0.5 * math.sin(t * (0.6 + load * 1.2) - i * 0.7)
        local red    = (i % 3 == 1)
        local n = math.max(30, math.floor(TWO_PI * r / 20))
        for j = 0, n - 1 do
            local a  = (j / n) * TWO_PI + t * (0.05 + i * 0.012 + load * 0.06)
            local dx = cx + r * math.cos(a)
            local dy = cy + r * math.sin(a)
            if dx > -4 and dx < w + 4 and dy > -4 and dy < h + 4 then
                local alpha
                if red then alpha = 0.10 + 0.22 * breath + 0.08 * load
                else        alpha = 0.06 + 0.11 * breath + 0.05 * load end
                cairo_set_source_rgba(cr, red and 0.84 or 0.80, red and 0.10 or 0.80,
                                          red and 0.13 or 0.85, alpha)
                cairo_arc(cr, dx, dy, red and 1.6 or 1.3, 0, TWO_PI)
                cairo_fill(cr)
            end
        end
    end

    -- 3) sonar ping — cadence quickens with CPU load
    local period = 5.0 - load * 2.5
    local frac = (t % period) / period
    local pr = frac * 1080
    local pa = (0.18 + 0.12 * load) * (1 - frac)
    if pa > 0.004 then
        cairo_set_line_width(cr, 1.4)
        cairo_set_source_rgba(cr, 0.84, 0.10, 0.13, pa)
        cairo_arc(cr, cx, cy, pr, 0, TWO_PI)
        cairo_stroke(cr)
    end

    -- 4) pulsing core dot (grows with load)
    cairo_set_source_rgba(cr, 0.84, 0.10, 0.13, 0.55 + 0.30 * math.sin(t * 1.4))
    cairo_arc(cr, cx, cy, 2.6 + 2 * load, 0, TWO_PI)
    cairo_fill(cr)

    cairo_destroy(cr)
    cairo_surface_destroy(cs)
end
