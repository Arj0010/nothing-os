-- Nothing-style dotted meter: filled ● / empty ·
function conky_dots(pct, n)
    pct = tonumber(pct) or 0
    n = tonumber(n) or 10
    local fill = math.floor(pct / 100 * n + 0.5)
    if fill > n then fill = n end
    if fill < 0 then fill = 0 end
    local s = ""
    for i = 1, n do
        s = s .. (i <= fill and "\u{25CF}" or "\u{00B7}")
    end
    return s
end
