
import { LightningElement, track } from 'lwc';

export default class LwcChildEvent extends LightningElement {
    @track param = {firstName: 'John', lastName: 'Week'};

    handleButtonClick() {
        const param = this.param;
        this.dispatchEvent(
            new CustomEvent('customevent', {
                detail: { param }
            })
        );
    }

}