# -*- coding: utf-8 -*-
"""网页注入脚本：自动填写账号密码并触发回车。

接替 V7 的行为：借助原生 setter 修改输入框，确保 Vue/React 等框架也能感知变化。
"""
INJECT_SCRIPT = r"""
(function() {
    function simulateInput(target, value) {
        if (!target) return false;
        try { target.focus(); } catch(e){}
        try {
            var proto = target.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
                                                      : window.HTMLInputElement.prototype;
            var valueSetter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            valueSetter.call(target, value);
        } catch(e) {
            try { target.value = value; } catch(e2) { return false; }
        }
        target.dispatchEvent(new Event('input', { bubbles: true }));
        target.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
    }

    function triggerEnter(target) {
        if (!target) return;
        ['keydown', 'keypress', 'keyup'].forEach(function(eventType) {
            target.dispatchEvent(new KeyboardEvent(eventType, {
                bubbles: true, cancelable: true,
                key: 'Enter', code: 'Enter', keyCode: 13, which: 13
            }));
        });
    }

    function visible(el) {
        return !!el && (el.offsetParent !== null || el.offsetWidth > 0 || el.offsetHeight > 0);
    }

    function findAndFill(account, password) {
        var activeTarget = null;
        var pwdInputs = document.querySelectorAll('input[type="password"]');
        for (var i = 0; i < pwdInputs.length; i++) {
            var pwd = pwdInputs[i];
            if (!visible(pwd)) continue;
            simulateInput(pwd, password);
            activeTarget = pwd;
            var form = pwd.form;
            if (form) {
                var formInputs = form.querySelectorAll(
                    'input:not([type="hidden"]):not([type="password"]):not([type="submit"]):not([type="button"])');
                if (formInputs.length > 0) {
                    simulateInput(formInputs[formInputs.length - 1], account);
                    continue;
                }
            }
            var candidates = document.querySelectorAll('input:not([type="hidden"]):not([type="password"])');
            var bestMatch = null;
            for (var j = 0; j < candidates.length; j++) {
                var txt = candidates[j];
                if (!visible(txt)) continue;
                var signature = (txt.id + txt.className + txt.placeholder + txt.name).toLowerCase();
                if (signature.indexOf('user') >= 0 || signature.indexOf('name') >= 0 ||
                    signature.indexOf('acc') >= 0 || signature.indexOf('号') >= 0) {
                    bestMatch = txt; break;
                }
                if (!bestMatch) bestMatch = txt;
            }
            if (bestMatch) simulateInput(bestMatch, account);
        }
        triggerEnter(activeTarget || document.activeElement);
        return true;
    }

    window.__fillV7 = function(account, password) { return findAndFill(account, password); };
})();
"""
